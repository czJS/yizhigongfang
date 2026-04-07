## Third-party data used by this project

This project vendors a small number of third-party **data files** to support Chinese phrase/suspect extraction in quality-mode review.

### Chinese idioms list (derived)

- **Upstream project**: `pwxcoo/chinese-xinhua`
- **Upstream file**: `data/idiom.json`
- **License**: MIT License (see upstream `LICENSE`)
- **Vendored output**: `assets/zh_phrase/idioms_4char.txt`
- **How it is generated**: extract `word` entries with `len(word)==4`, then de-duplicate and sort.

### Chinese homophone (same-pinyin) confusion table (optional)

- **Upstream project**: `shibing624/pycorrector`
- **Upstream file**: `pycorrector/data/same_pinyin.txt`
- **License**: Apache-2.0 (see upstream `LICENSE`)
- **Vendored file**: `assets/zh_phrase/pycorrector_same_pinyin.txt`
- **Used for**: optional idiom near-match (homophone-1) in quality-mode review (e.g., `神龙百尾` ≈ `神龙摆尾`). Not required for the core pipeline.

### Chinese lexicon for lightweight repair candidate validation

- **Upstream project**: `pwxcoo/chinese-xinhua`
- **Upstream file**: `data/ci.json`
- **License**: MIT License (see upstream `LICENSE`)
- **Vendored output**: `assets/zh_phrase/chinese_xinhua_ci_2to4.txt`
- **How it is generated**: extract pure-CJK `ci` entries with `2 <= len(ci) <= 4`, then de-duplicate and sort.
- **Used for**: local one-char repair candidate lookup / validation before falling back to the LLM.

### Proper-noun protection lexicon

- **Upstream project**: `thunlp/THUOCL`
- **Upstream files**: `data/THUOCL_diming.txt`, `data/THUOCL_lishimingren.txt`
- **License**: MIT License (see upstream `LICENSE`)
- **Vendored output**: `assets/zh_phrase/thuocl_proper_nouns.txt`
- **How it is generated**: merge place-name and historical-person entries, keep pure-CJK items with `2 <= len(word) <= 8`, then de-duplicate and sort.
- **Used for**: lightweight proper-noun protection so local repair does not recklessly rewrite likely names / terms.

