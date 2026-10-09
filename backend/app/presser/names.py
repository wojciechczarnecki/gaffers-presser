from collections import defaultdict

FALLBACK_NAME = "Manager"


def _first_word(manager_name: str) -> str:
    words = manager_name.split()
    return words[0] if words else FALLBACK_NAME


def _with_initial(manager_name: str) -> str:
    words = manager_name.split()
    if len(words) < 2:
        return _first_word(manager_name)
    return f"{words[0]} {words[-1][0]}."


def display_names(managers: dict[int, str], nicknames: dict[int, str]) -> dict[int, str]:
    names = {
        entry_id: nicknames.get(entry_id) or _first_word(manager_name)
        for entry_id, manager_name in managers.items()
    }
    groups: dict[str, list[int]] = defaultdict(list)
    for entry_id, name in names.items():
        groups[name].append(entry_id)
    for ids in groups.values():
        if len(ids) < 2:
            continue
        for entry_id in ids:
            if entry_id not in nicknames:
                names[entry_id] = _with_initial(managers[entry_id])
    taken = {names[entry_id] for entry_id in nicknames if entry_id in names}
    seen: set[str] = set()
    for entry_id in sorted(names):
        name = names[entry_id]
        if entry_id in nicknames:
            seen.add(name)
            continue
        candidate, number = name, 1
        while candidate in seen or candidate in taken:
            number += 1
            candidate = f"{name} {number}"
        names[entry_id] = candidate
        seen.add(candidate)
    return names
