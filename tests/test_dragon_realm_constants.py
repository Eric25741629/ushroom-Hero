from dragon_realm import constants as C


def test_event_types():
    assert (C.PVE, C.PVP, C.BOX, C.TRAP, C.BUFF, C.CAVE) == (1, 2, 3, 4, 5, 6)


def test_event_data_keys():
    assert C.K_BACK_KILL_TIME == 3
    assert C.K_IS_CHALLENGE == 4
    assert C.K_ROLE_ID == 5
    assert C.K_IS_ASK_HELP == 7


def test_choices():
    assert (C.CHOICE_ADVANCE, C.CHOICE_DETOUR, C.CHOICE_ASK_HELP) == (1, 2, 3)
