# Character rank animation assets

These files are the public, auditable media references for the S/A/B/C/D character-rank buttons hardcoded in `bridge/cards.py`.

Telegram custom-emoji set: `sttb_ranks_by_SillyTavernPunzmeBot`

## Rank mapping

| Rank | `custom_emoji_id` | Telegram asset | Public GIF reference |
|---|---:|---|---|
| S | `6176891226302718197` | `telegram/rank_S.webm` | `source/rank_S_transparent.gif` |
| A | `6176955294329871952` | `telegram/rank_A.webm` | `source/rank_A_transparent.gif` |
| B | `6178981105849344346` | `telegram/rank_B.webm` | `source/rank_B_transparent.gif` |
| C | `6177219258724917819` | `telegram/rank_C.webm` | `source/rank_C_transparent.gif` |
| D | `6176733025477337215` | `telegram/rank_D.webm` | `source/rank_D_transparent.gif` |

The checked-in WEBM files were downloaded back from the bot-owned Telegram custom-emoji set, so they are the exact registered media behind the hardcoded IDs. They are VP9, 100×100, and 2.1 seconds long. The checked-in GIF files are public reference derivatives generated from those registered WEBMs with `fps=12.5,scale=100:100:flags=lanczos` and infinite looping. This keeps the repository reference small while preserving the animation users see in Telegram.

## Original upload provenance

The custom-emoji set was created from the user-supplied transparent 320×320 GIF pack. The original upload is not required at runtime. Its per-rank SHA-256 values are recorded here for provenance:

| Original GIF | SHA-256 |
|---|---|
| `rank_S_transparent.gif` | `7971966fd429cf1321e66c559f12b2ca012e35321a263e07e899feb76dc907ad` |
| `rank_A_transparent.gif` | `f6d33a6f32de1bdbb95b484162bbe7bcd4b7b3008873da397ee236de131e4ce6` |
| `rank_B_transparent.gif` | `55539d8a321ef3f90fe0d9bae78956f2e4ef107bc3a9e27cd65b8f711ba13a75` |
| `rank_C_transparent.gif` | `5475444faa2d5bc8494e973c9db415b0c57f9f4c97aa14b1d606589388c5571e` |
| `rank_D_transparent.gif` | `b48c852bd64d673a245c49486bd967993ad14081b9018c42901ee57fa216b588` |

## Checked-in asset hashes

| File | SHA-256 |
|---|---|
| `source/rank_S_transparent.gif` | `18bfc6ba08abe3811878b4059a8d63e4613d0da6f9a272ac9155f355d781d8b2` |
| `source/rank_A_transparent.gif` | `8e38b025cd2b9f42130a0af849fcf83b27272be0d7fdfc59b88da1df9c2d3a7c` |
| `source/rank_B_transparent.gif` | `dfd710e678c7fcde1b7f8ef845d0e993ed89c53b417b817e41d8eb6f99def2bb` |
| `source/rank_C_transparent.gif` | `5bce587a7a4c93133628a89653b84fe416a8ee8947184bd06a9e6c8b042b0010` |
| `source/rank_D_transparent.gif` | `6bd89550f5b21fd74b268d394fe492295571413628f084bf796517604540022c` |
| `telegram/rank_S.webm` | `dca0c8a3e990b390578400e4fcf5402e0a8450a2730064bcad35192c3ebb2feb` |
| `telegram/rank_A.webm` | `79f7383e5be01e30e0c2e5e25c92d904196b2efc458d83f907564fd2347309ed` |
| `telegram/rank_B.webm` | `8fc75d3f2e3d33bf185511e76099c2491e974f9672e6f32aff64fd3a680d37b4` |
| `telegram/rank_C.webm` | `6d76db7540868a18d3b53d712e440658a431f3561c3975b072515f6079ad848a` |
| `telegram/rank_D.webm` | `ecf246f48ab6e4eac5c56517c21559b3ffe20680f226868840fab189b0985b06` |
