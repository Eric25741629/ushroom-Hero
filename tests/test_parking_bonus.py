from ws_token.parking_bonus import lot_bonus, parse_desc_bonus


def test_multi_bonus_desc_splits_by_clause():
    b = parse_desc_bonus("在本人私人車位獲取菇車幣和改裝點收益提高##1%，獲取額外奇遇獎勵概率提高##2%", [5,3])
    assert b == {"coin":5,"mod":5,"spec":3,"protect":0}


def test_battle_desc_contributes_nothing():
    assert parse_desc_bonus("在菇菇車位中戰鬥時攻擊提高##1%", [8]) == {"coin":0,"mod":0,"spec":0,"protect":0}


def test_flash_lot_bonus_matches_145_72_68():
    from tests.fixtures.mount_tracker_fixtures import FLASH_SKIN_LIST
    b = lot_bonus(FLASH_SKIN_LIST)
    assert (b["coin"], b["mod"], b["spec"]) == (145, 72, 68)
