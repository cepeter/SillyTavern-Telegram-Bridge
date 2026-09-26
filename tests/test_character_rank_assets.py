from __future__ import annotations

import hashlib
from pathlib import Path

from bridge.cards import _CHARACTER_RANK_CUSTOM_EMOJI_IDS

ROOT = Path(__file__).resolve().parents[1] / "assets" / "character-ranks"
EXPECTED_IDS = {
    "S": "6176891226302718197",
    "A": "6176955294329871952",
    "B": "6178981105849344346",
    "C": "6177219258724917819",
    "D": "6176733025477337215",
}
EXPECTED_SHA256 = {
    "source/rank_A_transparent.gif": "8e38b025cd2b9f42130a0af849fcf83b27272be0d7fdfc59b88da1df9c2d3a7c",
    "source/rank_B_transparent.gif": "dfd710e678c7fcde1b7f8ef845d0e993ed89c53b417b817e41d8eb6f99def2bb",
    "source/rank_C_transparent.gif": "5bce587a7a4c93133628a89653b84fe416a8ee8947184bd06a9e6c8b042b0010",
    "source/rank_D_transparent.gif": "6bd89550f5b21fd74b268d394fe492295571413628f084bf796517604540022c",
    "source/rank_S_transparent.gif": "18bfc6ba08abe3811878b4059a8d63e4613d0da6f9a272ac9155f355d781d8b2",
    "telegram/rank_A.webm": "79f7383e5be01e30e0c2e5e25c92d904196b2efc458d83f907564fd2347309ed",
    "telegram/rank_B.webm": "8fc75d3f2e3d33bf185511e76099c2491e974f9672e6f32aff64fd3a680d37b4",
    "telegram/rank_C.webm": "6d76db7540868a18d3b53d712e440658a431f3561c3975b072515f6079ad848a",
    "telegram/rank_D.webm": "ecf246f48ab6e4eac5c56517c21559b3ffe20680f226868840fab189b0985b06",
    "telegram/rank_S.webm": "dca0c8a3e990b390578400e4fcf5402e0a8450a2730064bcad35192c3ebb2feb",
}


def test_public_rank_assets_match_hardcoded_custom_emoji_mapping():
    assert _CHARACTER_RANK_CUSTOM_EMOJI_IDS == EXPECTED_IDS
    manifest = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "sttb_ranks_by_SillyTavernPunzmeBot" in manifest
    for tier, custom_emoji_id in EXPECTED_IDS.items():
        assert f"{tier} | `{custom_emoji_id}`" in manifest
    for relative, digest in EXPECTED_SHA256.items():
        path = ROOT / relative
        assert path.is_file(), relative
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        assert digest in manifest
