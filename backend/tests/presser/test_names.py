from app.presser.names import display_names


def test_nickname_wins():
    assert display_names({1: "Tomasz Kowalski"}, {1: "Bartas"}) == {1: "Bartas"}


def test_first_word_of_manager_name():
    assert display_names({1: "Tomasz Kowalski", 2: "Anna Nowak"}, {}) == {1: "Tomasz", 2: "Anna"}


def test_collision_on_first_word_uses_last_word_initial():
    names = display_names({1: "Tomasz Kowalski", 2: "Tomasz Wiśniewski"}, {})
    assert names == {1: "Tomasz K.", 2: "Tomasz W."}


def test_collision_on_initial_too_gets_number_by_entry_id():
    names = display_names({5: "Tomasz Kowalski", 2: "Tomasz Kamiński", 9: "Anna Nowak"}, {})
    assert names == {2: "Tomasz K.", 5: "Tomasz K. 2", 9: "Anna"}


def test_nickname_keeps_priority_and_is_never_changed():
    names = display_names({1: "Tomasz Kowalski", 2: "Tomasz Wiśniewski"}, {1: "Tomasz"})
    assert names[1] == "Tomasz"
    assert names[2] != "Tomasz"
    assert len(set(names.values())) == 2


def test_empty_manager_name_falls_back_to_manager():
    assert display_names({1: "", 2: "  "}, {}) == {1: "Manager", 2: "Manager 2"}


def test_single_word_name_collision_gets_number():
    assert display_names({1: "Tomasz", 2: "Tomasz"}, {}) == {1: "Tomasz", 2: "Tomasz 2"}
