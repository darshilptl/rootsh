manifest a357bba6c02c, bundle ca33283a3dde, model full 9d317325fd7b; validation rows: {'authorized': 36, 'refused': 34}

| layer | decision | authorized | refused |
|---|---|---|---|
| untrusted | block | 0 | 1 |
| read_only | allow | 14 | 4 |
| project_edit | allow | 8 | 5 |
| model | ask | 12 | 17 |
| model | block | 2 | 7 |

| policy | allow auth | ask auth | block auth | allow refused (false allow) | ask refused | block refused | asks per 1k authorized |
|---|---|---|---|---|---|---|---|
| P7 policy | 22 | 12 | 2 | 9 | 17 | 8 | 333 |
| model alone (P6) | 0 | 34 | 2 | 0 | 26 | 8 | 944 |
