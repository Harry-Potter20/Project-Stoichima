"""
Bi-LSTM match outcome model — PyTorch implementation.

Architecture:
    home_seq (batch, window, 8) → shared BiLSTM → home_embed (batch, 128)
    away_seq (batch, window, 8) → shared BiLSTM → away_embed (batch, 128)
    concat([home_embed, away_embed]) → (batch, 256)
    → Linear(256, 128) → ReLU → Dropout(0.3)
    → Linear(128, 3)   → softmax   [H, D, A]

Shared weights between home and away encoders force the model to learn a
team-agnostic "form embedding", letting the match head learn the matchup.
"""

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
import pandas as pd

from data_processing.sequence_builder import build_sequences, to_tensors, DEFAULT_WINDOW, N_FEATURES

HIDDEN_SIZE   = 64
N_LAYERS      = 2
DROPOUT       = 0.3
N_CLASSES     = 3          # H / D / A
BATCH_SIZE    = 128
N_EPOCHS      = 50
LR            = 1e-3
PATIENCE      = 7          # early stopping
CUTOFF_SEASON = 2023


class _TeamEncoder(nn.Module):
    """Bi-LSTM that maps a sequence of match features to a fixed-size embedding."""

    def __init__(self, input_size: int, hidden_size: int, n_layers: int, dropout: float):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=n_layers,
            batch_first=True,
            bidirectional=True,
            dropout=dropout if n_layers > 1 else 0.0,
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (batch, seq_len, input_size)
        # h_n: (n_layers*2, batch, hidden_size) — take last layer both directions
        _, (h_n, _) = self.lstm(x)
        # h_n[-2]: forward last layer, h_n[-1]: backward last layer
        fwd = h_n[-2]   # (batch, hidden_size)
        bwd = h_n[-1]   # (batch, hidden_size)
        return torch.cat([fwd, bwd], dim=1)   # (batch, hidden_size*2)


class _MatchHead(nn.Module):
    def __init__(self, embed_size: int, n_classes: int, dropout: float):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(embed_size * 2, 128),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(128, n_classes),
        )

    def forward(self, home_emb: torch.Tensor, away_emb: torch.Tensor) -> torch.Tensor:
        x = torch.cat([home_emb, away_emb], dim=1)
        return self.net(x)   # (batch, n_classes) — raw logits


class _BiLSTMNet(nn.Module):
    def __init__(self):
        super().__init__()
        embed_size = HIDDEN_SIZE * 2   # bidirectional doubles output
        self.encoder = _TeamEncoder(N_FEATURES, HIDDEN_SIZE, N_LAYERS, DROPOUT)
        self.head     = _MatchHead(embed_size, N_CLASSES, DROPOUT)

    def forward(self, home_seq: torch.Tensor, away_seq: torch.Tensor) -> torch.Tensor:
        home_emb = self.encoder(home_seq)
        away_emb = self.encoder(away_seq)
        return self.head(home_emb, away_emb)


class BiLSTMModel:
    """
    Public interface matching the sklearn-style API used by the ensemble.

    Usage:
        model = BiLSTMModel()
        model.train(df)                     # df has finished matches with result column
        proba = model.predict_proba(df)     # shape (n, 3) — [H, D, A]
        model.save("saved_models/bilstm_model.pt")

        model2 = BiLSTMModel()
        model2.load("saved_models/bilstm_model.pt")
    """

    def __init__(self, window: int = DEFAULT_WINDOW):
        self.window = window
        self.net: _BiLSTMNet | None = None
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        # We store the full df used for building history at inference time
        self._history_df: pd.DataFrame | None = None

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def train(self, df: pd.DataFrame) -> None:
        # macOS: XGBoost and PyTorch each ship libomp. If XGBoost ran first its
        # OpenMP pool is already initialised; forcing single-threaded PyTorch
        # avoids the deadlock when PyTorch tries to spawn its own pool.
        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)

        df = df.sort_values("match_date").reset_index(drop=True)
        self._history_df = df.copy()

        home_seqs, away_seqs, labels = build_sequences(df, self.window)

        # Only rows that have a valid label
        valid = labels >= 0
        H = torch.from_numpy(home_seqs[valid])
        A = torch.from_numpy(away_seqs[valid])
        Y = torch.from_numpy(labels[valid])

        # Train / val split by season cutoff
        seasons = df[valid]["season"].values
        train_mask = seasons <  CUTOFF_SEASON
        val_mask   = seasons >= CUTOFF_SEASON

        train_ds = TensorDataset(H[train_mask], A[train_mask], Y[train_mask])
        val_ds   = TensorDataset(H[val_mask],   A[val_mask],   Y[val_mask])
        train_dl = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True)
        val_dl   = DataLoader(val_ds,   batch_size=BATCH_SIZE)

        # macOS: PyTorch and XGBoost each ship libomp; when XGBoost runs first
        # its thread pool is already initialised and PyTorch deadlocks trying to
        # create its own. Single-threaded PyTorch avoids the conflict entirely.
        torch.set_num_threads(1)
        self.net = _BiLSTMNet().to(self.device)
        optimiser = torch.optim.Adam(self.net.parameters(), lr=LR)

        # Class weights: inverse-frequency so the model doesn't collapse to H/A only.
        label_counts = np.bincount(labels[labels >= 0], minlength=3).astype(np.float32)
        label_counts = np.where(label_counts == 0, 1.0, label_counts)
        class_weights = torch.tensor(label_counts.sum() / (3.0 * label_counts)).to(self.device)
        criterion = nn.CrossEntropyLoss(weight=class_weights)

        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimiser, patience=3, factor=0.5
        )

        best_val_loss = float("inf")
        epochs_no_improve = 0
        best_state = None

        for epoch in range(1, N_EPOCHS + 1):
            self.net.train()
            train_loss = 0.0
            for hb, ab, yb in train_dl:
                hb, ab, yb = hb.to(self.device), ab.to(self.device), yb.to(self.device)
                optimiser.zero_grad()
                logits = self.net(hb, ab)
                loss = criterion(logits, yb)
                loss.backward()
                nn.utils.clip_grad_norm_(self.net.parameters(), 1.0)
                optimiser.step()
                train_loss += loss.item() * len(yb)

            val_loss, val_correct = 0.0, 0
            self.net.eval()
            with torch.no_grad():
                for hb, ab, yb in val_dl:
                    hb, ab, yb = hb.to(self.device), ab.to(self.device), yb.to(self.device)
                    logits = self.net(hb, ab)
                    val_loss += criterion(logits, yb).item() * len(yb)
                    val_correct += (logits.argmax(1) == yb).sum().item()

            n_train = len(train_ds)
            n_val   = len(val_ds)
            val_loss_avg = val_loss / max(n_val, 1)
            val_acc = val_correct / max(n_val, 1)
            scheduler.step(val_loss_avg)

            if val_loss_avg < best_val_loss:
                best_val_loss = val_loss_avg
                best_state    = {k: v.clone() for k, v in self.net.state_dict().items()}
                epochs_no_improve = 0
            else:
                epochs_no_improve += 1

            if epoch % 10 == 0:
                print(f"    epoch {epoch:3d}  train_loss={train_loss/n_train:.4f}"
                      f"  val_loss={val_loss_avg:.4f}  val_acc={val_acc:.3f}")

            if epochs_no_improve >= PATIENCE:
                print(f"    early stop at epoch {epoch}")
                break

        if best_state is not None:
            self.net.load_state_dict(best_state)

        print(f"    best val_loss={best_val_loss:.4f}  val_acc={val_acc:.3f}")

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def _trim_history(self, teams: set) -> pd.DataFrame:
        """Return last (window * 3) matches per team from stored history."""
        if self._history_df is None or self._history_df.empty:
            return pd.DataFrame()
        hist = self._history_df.sort_values("match_date")
        needed: set = set()
        for team in teams:
            mask = (hist["home_team"] == team) | (hist["away_team"] == team)
            needed.update(hist[mask].tail(self.window * 3).index.tolist())
        return hist.loc[sorted(needed)]

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        """
        For each row in df (may be upcoming matches with result=None),
        builds sequences using stored history + the rows themselves, then
        returns shape (n, 3) probabilities [H, D, A].
        """
        if self._history_df is None:
            raise RuntimeError("Call train() or load() before predict_proba()")

        # Trim history to teams involved so build_sequences runs over ~hundreds
        # of rows instead of the full 122k training history (iterrows bottleneck)
        teams = set(df["home_team"].tolist() + df["away_team"].tolist())
        history_trimmed = self._trim_history(teams)
        combined = pd.concat([history_trimmed, df], ignore_index=True).sort_values("match_date")
        home_seqs, away_seqs, _ = build_sequences(combined, self.window)

        # We only want the last len(df) rows
        n = len(df)
        h = torch.from_numpy(home_seqs[-n:]).to(self.device)
        a = torch.from_numpy(away_seqs[-n:]).to(self.device)

        self.net.eval()
        with torch.no_grad():
            logits = self.net(h, a)
            proba = torch.softmax(logits, dim=1).cpu().numpy()
        return proba   # (n, 3) ordered [H, D, A]

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        proba = self.predict_proba(df)
        labels = ["H", "D", "A"]
        return np.array([labels[i] for i in proba.argmax(axis=1)])

    def evaluate(self, df: pd.DataFrame) -> dict:
        from sklearn.metrics import classification_report
        test_df = df[df["season"] >= CUTOFF_SEASON].dropna(subset=["result"])
        if test_df.empty:
            return {}
        proba = self.predict_proba(test_df)
        label_map = {"H": 0, "D": 1, "A": 2}
        y_true = test_df["result"].map(label_map).values
        y_pred = proba.argmax(axis=1)
        report = classification_report(y_true, y_pred, output_dict=True)
        return {
            "bilstm_accuracy":    report["accuracy"],
            "bilstm_macro_f1":    report["macro avg"]["f1-score"],
            "bilstm_weighted_f1": report["weighted avg"]["f1-score"],
            "bilstm_test_n":      len(test_df),
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        # Trim to last (window * 3) matches per team before saving — keeps file
        # small and makes predict_proba fast even on a freshly loaded model.
        if self._history_df is not None and not self._history_df.empty:
            all_teams = (
                set(self._history_df["home_team"].tolist()) |
                set(self._history_df["away_team"].tolist())
            )
            history_to_save = self._trim_history(all_teams)
        else:
            history_to_save = self._history_df
        torch.save({
            "state_dict":  self.net.state_dict(),
            "window":      self.window,
            "history_df":  history_to_save,
        }, path)

    def load(self, path: str) -> None:
        # Prevent deadlock: XGBoost initialises its OpenMP pool before the first
        # multi-row prediction; if PyTorch tries to create its own pool afterwards
        # on macOS it deadlocks. Single-threaded PyTorch avoids the conflict.
        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
        checkpoint = torch.load(path, map_location=self.device, weights_only=False)
        self.window       = checkpoint["window"]
        self._history_df  = checkpoint["history_df"]
        self.net = _BiLSTMNet().to(self.device)
        self.net.load_state_dict(checkpoint["state_dict"])
        self.net.eval()
