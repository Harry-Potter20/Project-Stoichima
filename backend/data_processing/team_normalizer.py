

EXCEPTIONS = {
    "FC Barcelona": "Barcelona",
    "Manchester United": "Man United",
    "Manchester City": "Man City",
    "Wolverhampton Wanderers": "Wolves",
    "Brighton & Hove Albion": "Brighton",
    "Newcastle United": "Newcastle", 
    "Tottenham Hotspur": "Tottenham",
    "Nottingham Forest": "Nott'm Forest",
    "AFC Bournemouth": "Bournemouth",
    "West Ham United": "West Ham",
    "Leeds United": "Leeds"
}
PREFIXES = ["FC ", "CF ", "AFC ", "SC "]
SUFFIXES = [" FC", " CF", " AFC", " SC"]

def normalise_team_name(name: str) -> str:
    name = name.strip()
    # Remove known prefixes
    for prefix in PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    # Remove known suffixes
    for suffix in SUFFIXES:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    return EXCEPTIONS.get(name, name)