"""
Stop-gap manual seeding of national-team squads, used when the API-Football
quota is exhausted. Lets /props work for the top WC contenders the same day.

Players are selected to match how they appear in PlayerMatchStats (Understat
naming). Update this list any time a major player changes clubs or retires.

Run:
    python -m data_collection.squad_seed

Idempotent — upserts on (nation, player_name).
"""
from __future__ import annotations
import logging
from app.database import SessionLocal
from app.models import NationalTeamSquad

log = logging.getLogger(__name__)


# Curated short rosters — focus on likely scorers + key attacking players.
# Names match Understat's player naming convention so the PMS lookup hits.
SEEDED_SQUADS: dict[str, list[str]] = {
    "Brazil": [
        "Vinicius Junior", "Rodrygo", "Raphinha", "Endrick", "Bruno Guimaraes",
        "Lucas Paqueta", "Casemiro", "Antony", "Richarlison", "Neymar",
        "Eder Militao", "Marquinhos", "Alisson",
    ],
    "Argentina": [
        "Lionel Messi", "Lautaro Martinez", "Julian Alvarez", "Angel Di Maria",
        "Paulo Dybala", "Alexis Mac Allister", "Enzo Fernandez", "Rodrigo De Paul",
        "Nicolas Tagliafico", "Cristian Romero", "Emiliano Martinez",
    ],
    "France": [
        "Kylian Mbappe", "Antoine Griezmann", "Ousmane Dembele", "Kingsley Coman",
        "Marcus Thuram", "Olivier Giroud", "Eduardo Camavinga", "Aurelien Tchouameni",
        "Adrien Rabiot", "William Saliba", "Theo Hernandez",
    ],
    "Spain": [
        "Lamine Yamal", "Nico Williams", "Alvaro Morata", "Mikel Oyarzabal",
        "Dani Olmo", "Pedri", "Rodri", "Fermin Lopez", "Ferran Torres",
        "Pau Cubarsi", "Aymeric Laporte",
    ],
    "Germany": [
        "Jamal Musiala", "Florian Wirtz", "Kai Havertz", "Niclas Fullkrug",
        "Leroy Sane", "Serge Gnabry", "Toni Kroos", "Joshua Kimmich",
        "Ilkay Gundogan", "Antonio Rudiger",
    ],
    "England": [
        "Harry Kane", "Phil Foden", "Jude Bellingham", "Bukayo Saka",
        "Marcus Rashford", "Cole Palmer", "Ollie Watkins", "Anthony Gordon",
        "Declan Rice", "Trent Alexander-Arnold", "Kyle Walker",
    ],
    "Portugal": [
        "Cristiano Ronaldo", "Bruno Fernandes", "Joao Felix", "Bernardo Silva",
        "Rafael Leao", "Diogo Jota", "Goncalo Ramos", "Pedro Neto",
        "Vitinha", "Bernardo Silva", "Ruben Dias",
    ],
    "Netherlands": [
        "Memphis Depay", "Cody Gakpo", "Wout Weghorst", "Donyell Malen",
        "Xavi Simons", "Tijjani Reijnders", "Frenkie de Jong", "Steven Bergwijn",
        "Denzel Dumfries", "Virgil van Dijk",
    ],
    "Belgium": [
        "Kevin De Bruyne", "Romelu Lukaku", "Jeremy Doku", "Yannick Carrasco",
        "Leandro Trossard", "Charles De Ketelaere", "Kevin De Bruyne",
        "Youri Tielemans", "Amadou Onana",
    ],
    "Italy": [
        "Lorenzo Pellegrini", "Mateo Retegui", "Federico Chiesa", "Gianluca Scamacca",
        "Nicolo Barella", "Jorginho", "Marco Verratti", "Sandro Tonali",
        "Alessandro Bastoni",
    ],
    "Croatia": [
        "Luka Modric", "Mateo Kovacic", "Marcelo Brozovic", "Andrej Kramaric",
        "Bruno Petkovic", "Ante Budimir", "Ivan Perisic",
    ],
    "Morocco": [
        "Achraf Hakimi", "Hakim Ziyech", "Sofiane Boufal", "Youssef En-Nesyri",
        "Romain Saiss", "Sofyan Amrabat", "Azzedine Ounahi",
    ],
    "Senegal": [
        "Sadio Mane", "Ismaila Sarr", "Boulaye Dia", "Nicolas Jackson",
        "Edouard Mendy", "Kalidou Koulibaly", "Idrissa Gueye", "Krepin Diatta",
    ],
    "United States": [
        "Christian Pulisic", "Folarin Balogun", "Weston McKennie", "Tyler Adams",
        "Yunus Musah", "Brenden Aaronson", "Tim Weah", "Antonee Robinson",
    ],
    "Mexico": [
        "Hirving Lozano", "Raul Jimenez", "Edson Alvarez", "Hector Herrera",
        "Santiago Gimenez", "Cesar Montes",
    ],
    "Japan": [
        "Takefusa Kubo", "Daichi Kamada", "Daizen Maeda", "Kaoru Mitoma",
        "Wataru Endo", "Takehiro Tomiyasu", "Takumi Minamino",
    ],
    "South Korea": [
        "Son Heung-Min", "Hwang Hee-Chan", "Lee Kang-In", "Hwang Ui-Jo",
        "Kim Min-Jae",
    ],
    "Australia": [
        "Mathew Leckie", "Jackson Irvine", "Mitchell Duke", "Riley McGree",
        "Aaron Mooy",
    ],
    "Algeria": [
        "Riyad Mahrez", "Islam Slimani", "Said Benrahma", "Youcef Belaili",
        "Ismael Bennacer",
    ],
    "Nigeria": [
        "Victor Osimhen", "Ademola Lookman", "Samuel Chukwueze", "Moses Simon",
        "Wilfred Ndidi", "Alex Iwobi", "Taiwo Awoniyi",
    ],
    "Egypt": [
        "Mohamed Salah", "Trezeguet", "Omar Marmoush", "Mostafa Mohamed",
        "Mohamed Elneny",
    ],
    "Switzerland": [
        "Granit Xhaka", "Breel Embolo", "Xherdan Shaqiri", "Ruben Vargas",
        "Zeki Amdouni", "Manuel Akanji", "Remo Freuler",
    ],
    "Denmark": [
        "Christian Eriksen", "Rasmus Hojlund", "Pierre-Emile Hojbjerg",
        "Andreas Skov Olsen", "Jonas Wind", "Joachim Andersen", "Mikkel Damsgaard",
    ],
    "Uruguay": [
        "Darwin Nunez", "Federico Valverde", "Maxi Gomez", "Facundo Pellistri",
        "Nicolas De La Cruz", "Rodrigo Bentancur",
    ],
    "Colombia": [
        "Luis Diaz", "James Rodriguez", "Jhon Duran", "Rafael Borre",
        "Jefferson Lerma", "Davinson Sanchez",
    ],
    "Iran": [
        "Sardar Azmoun", "Mehdi Taremi", "Alireza Jahanbakhsh", "Saman Ghoddos",
        "Karim Ansarifard",
    ],
}


def run() -> int:
    n_added = 0
    with SessionLocal() as db:
        for nation, players in SEEDED_SQUADS.items():
            for player_name in dict.fromkeys(players):   # dedup, preserve order
                existing = (
                    db.query(NationalTeamSquad)
                    .filter(
                        NationalTeamSquad.nation      == nation,
                        NationalTeamSquad.player_name == player_name,
                    )
                    .first()
                )
                if existing:
                    continue
                db.add(NationalTeamSquad(
                    nation      = nation,
                    player_name = player_name,
                    source      = "manual_seed",
                ))
                n_added += 1
        db.commit()
    return n_added


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    n = run()
    print(f"Seeded {n} new squad entries across {len(SEEDED_SQUADS)} nations")
