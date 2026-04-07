#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple


def _shuffle_options(
    rng: random.Random,
    *,
    correct_text: str,
    distractors: Sequence[str],
) -> Tuple[List[Dict[str, str]], str]:
    labels = ["A", "B", "C"]
    texts = [correct_text, *list(distractors)[:2]]
    rng.shuffle(texts)
    options = [{"id": labels[idx], "text": text} for idx, text in enumerate(texts)]
    label = labels[texts.index(correct_text)]
    return options, label


def _build_terminology_rows(rng: random.Random) -> List[Dict[str, Any]]:
    specs = [
        ("阿诺", "Arnold", "Arno", "Arnald", "Chinese source: 阿诺终于走出了牢房。 Current English sentence: Arno finally stepped out of the cell. Name under review: 阿诺. Character list: 阿诺 -> Arnold. Earlier subtitles already used 'Arnold'. Choose the consistent name."),
        ("六分仪", "sextant", "six-point meter", "measuring compass", "Chinese source: 他把六分仪塞进了包里。 Current English sentence: He stuffed the six-point meter into the bag. Term under review: 六分仪. Glossary: 六分仪 -> sextant. Earlier subtitles already used 'sextant'. Choose the consistent term."),
        ("典狱长", "warden", "prison chief", "jail boss", "Chinese source: 典狱长让所有人安静。 Current English sentence: The prison chief told everyone to be quiet. Title under review: 典狱长. Glossary: 典狱长 -> warden. Earlier subtitles already used 'warden'. Choose the consistent title."),
        ("守卫", "guard", "watchman", "side guard", "Chinese source: 守卫转身离开了。 Current English sentence: The watchman turned and left. Term under review: 守卫. Earlier subtitles already used 'guard'. Choose the consistent wording."),
        ("悬赏金", "bounty", "reward", "bonus", "Chinese source: 他们为他开出了悬赏金。 Current English sentence: They offered a reward for him. Term under review: 悬赏金. Earlier subtitles already used 'bounty'. Choose the consistent term."),
        ("瞭望塔", "watchtower", "lookout tower", "signal tower", "Chinese source: 他从瞭望塔上跳了下来。 Current English sentence: He jumped down from the lookout tower. Term under review: 瞭望塔. Glossary: 瞭望塔 -> watchtower. Earlier subtitles already used 'watchtower'. Choose the consistent term."),
        ("禁闭室", "solitary cell", "punishment room", "closed room", "Chinese source: 他被扔进了禁闭室。 Current English sentence: He was thrown into the punishment room. Term under review: 禁闭室. Earlier subtitles already used 'solitary cell'. Choose the consistent term."),
        ("医务室", "infirmary", "medical room", "clinic room", "Chinese source: 她把他送去了医务室。 Current English sentence: She took him to the medical room. Term under review: 医务室. Earlier subtitles already used 'infirmary'. Choose the consistent term."),
        ("补给船", "supply ship", "cargo boat", "supply boat", "Chinese source: 补给船明天一早会靠岸。 Current English sentence: The cargo boat will dock tomorrow morning. Term under review: 补给船. Earlier subtitles already used 'supply ship'. Choose the consistent term."),
        ("值夜", "night watch", "night shift", "night guard", "Chinese source: 今晚轮到他值夜。 Current English sentence: He is on the night shift tonight. Term under review: 值夜. Earlier subtitles already used 'night watch'. Choose the consistent wording."),
        ("越狱", "escape", "break prison", "jailbreak", "Chinese source: 所有人都在谈论那次越狱。 Current English sentence: Everyone was talking about that jailbreak. Term under review: 越狱. Earlier subtitles consistently used the verb noun 'escape'. Choose the consistent wording."),
        ("牢房", "cell", "cell room", "prison room", "Chinese source: 他回到了自己的牢房。 Current English sentence: He returned to his prison room. Term under review: 牢房. Earlier subtitles already used 'cell'. Choose the consistent term."),
        ("码头", "dock", "pier", "harbor side", "Chinese source: 他们在码头等了一整夜。 Current English sentence: They waited all night by the pier. Term under review: 码头. Earlier subtitles already used 'dock'. Choose the consistent term."),
        ("审讯室", "interrogation room", "question room", "interview room", "Chinese source: 他被带进了审讯室。 Current English sentence: He was taken into the question room. Term under review: 审讯室. Earlier subtitles already used 'interrogation room'. Choose the consistent term."),
        ("逃生通道", "escape tunnel", "emergency tunnel", "exit tunnel", "Chinese source: 他们找到了逃生通道。 Current English sentence: They found the emergency tunnel. Term under review: 逃生通道. Earlier subtitles already used 'escape tunnel'. Choose the consistent term."),
        ("海图", "sea chart", "map", "sailing map", "Chinese source: 海图被他藏在床下。 Current English sentence: He hid the sailing map under the bed. Term under review: 海图. Earlier subtitles already used 'sea chart'. Choose the consistent term."),
        ("副典狱长", "deputy warden", "assistant warden", "vice jailer", "Chinese source: 副典狱长先赶到了现场。 Current English sentence: The assistant warden arrived first. Title under review: 副典狱长. Earlier subtitles already used 'deputy warden'. Choose the consistent title."),
        ("禁区", "restricted area", "forbidden zone", "closed area", "Chinese source: 这里是禁区。 Current English sentence: This is the forbidden zone. Term under review: 禁区. Earlier subtitles already used 'restricted area'. Choose the consistent term."),
        ("风暴季", "storm season", "rainy season", "storm time", "Chinese source: 风暴季快到了。 Current English sentence: Storm time is coming. Term under review: 风暴季. Earlier subtitles already used 'storm season'. Choose the consistent term."),
        ("哨子", "whistle", "signal whistle", "pipe", "Chinese source: 守卫吹响了哨子。 Current English sentence: The guard blew the signal whistle. Term under review: 哨子. Earlier subtitles already used 'whistle'. Choose the consistent term."),
    ]
    rows: List[Dict[str, Any]] = []
    for idx, (_, correct, wrong1, wrong2, task) in enumerate(specs, start=1):
        options, label = _shuffle_options(rng, correct_text=correct, distractors=[wrong1, wrong2])
        rows.append(
            {
                "id": f"term_{idx:03d}",
                "capability": "terminology_consistency",
                "task": task,
                "options": options,
                "label": label,
            }
        )
    return rows


def _build_readability_rows(rng: random.Random) -> List[Dict[str, Any]]:
    specs = [
        ("1.4", "Arnold hid the sextant just in time.", "Fortunately, Arnold was actually able to hide the sextant just in time.", "Arnold hid the sextant just in time.", "Arnold hid it."),
        ("2.1", "She knocked on the glass to get his attention.", "At that very moment, she deliberately knocked on the glass in order to attract his attention.", "She knocked on the glass to get his attention.", "She knocked on the glass."),
        ("1.8", "He could not go out or talk to anyone.", "He was not allowed to go outside and he was not allowed to talk to anyone.", "He couldn't go out or talk to anyone.", "He couldn't talk."),
        ("2.0", "The challenge made him even more excited.", "Because the challenge in front of him was so great, he became even more excited.", "The challenge made him even more excited.", "He got excited."),
        ("1.6", "It was in the surveillance room.", "It was in the surveillance room at that exact point in time.", "It was in the surveillance room.", "It was there."),
        ("1.7", "They waited all night by the dock.", "They remained by the dock for the entire duration of the night.", "They waited all night by the dock.", "They waited there."),
        ("1.9", "The guard checked every cell.", "The guard went ahead and carefully checked every single cell one by one.", "The guard checked every cell.", "The guard checked."),
        ("1.5", "He hid the key under the bed.", "He quickly and carefully hid the key underneath the bed before anyone could see it.", "He hid the key under the bed.", "He hid the key."),
        ("2.2", "The deputy warden arrived first.", "Before anyone else could get there, the deputy warden arrived at the scene first.", "The deputy warden arrived first.", "He arrived first."),
        ("1.8", "The storm season was coming.", "It was becoming increasingly clear that the storm season was about to arrive.", "Storm season was coming.", "A storm was coming."),
        ("2.0", "They found the escape tunnel.", "After searching for a long time, they finally found the escape tunnel.", "They found the escape tunnel.", "They found a tunnel."),
        ("1.6", "He locked the door from inside.", "He made sure to lock the door from the inside before leaving the room.", "He locked the door from inside.", "He locked the door."),
        ("2.1", "The boat would dock at dawn.", "According to the schedule, the boat would finally dock around dawn the next morning.", "The boat would dock at dawn.", "The boat would come."),
        ("1.5", "She kept her eyes on the watchtower.", "Throughout the entire scene, she kept her eyes fixed firmly on the watchtower.", "She kept her eyes on the watchtower.", "She watched it."),
        ("1.9", "He was thrown into solitary cell.", "As punishment, he was immediately thrown into the solitary cell without any warning.", "He was thrown into solitary cell.", "He was punished."),
        ("2.0", "Everyone was talking about the escape.", "After the incident, everyone in the prison kept talking about the escape.", "Everyone was talking about the escape.", "Everyone talked about it."),
        ("1.7", "He copied the sea chart by hand.", "He carefully copied the entire sea chart by hand late into the night.", "He copied the sea chart by hand.", "He copied it."),
        ("1.8", "The whistle echoed through the hall.", "The sharp sound of the whistle echoed loudly through the whole hall.", "The whistle echoed through the hall.", "The whistle echoed."),
        ("2.2", "She sent him to the infirmary.", "Because he looked weak, she immediately sent him over to the infirmary for treatment.", "She sent him to the infirmary.", "She sent him away."),
        ("1.6", "The restricted area was empty.", "At that moment, the restricted area happened to be completely empty of people.", "The restricted area was empty.", "It was empty."),
    ]
    rows: List[Dict[str, Any]] = []
    for idx, (duration, meaning, long_text, balanced_text, short_text) in enumerate(specs, start=1):
        options, label = _shuffle_options(rng, correct_text=balanced_text, distractors=[long_text, short_text])
        rows.append(
            {
                "id": f"read_{idx:03d}",
                "capability": "subtitle_readability",
                "task": f"Choose the most subtitle-friendly line that preserves the key meaning. Duration: {duration} seconds. Source meaning: {meaning}",
                "options": options,
                "label": label,
            }
        )
    return rows


def _build_tts_rows(rng: random.Random) -> List[Dict[str, Any]]:
    specs = [
        ("We stored it in SQL.", "We stored it in S Q L.", "We stored it in sequel."),
        ("The part weighs 12kg.", "The part weighs twelve kilograms.", "The part weighs one two kg."),
        ("The bonus was $3.5M.", "The bonus was three point five million dollars.", "The bonus was three point five M."),
        ("It was built with a 3D printer.", "It was built with a three-D printer.", "It was built with a three printer."),
        ("He worked for the U.S. Navy.", "He worked for the U S Navy.", "He worked for the us Navy."),
        ("The update is in v2.4.1.", "The update is in version two point four point one.", "The update is in vee two forty one."),
        ("Send it through the API.", "Send it through the A P I.", "Send it through the appy."),
        ("The package is 7.2GB.", "The package is seven point two gigabytes.", "The package is seven two G B."),
        ("Her ID is A17.", "Her I D is A one seven.", "Her ID is ay seventeen."),
        ("The meeting starts at 6:30 p.m.", "The meeting starts at six thirty P M.", "The meeting starts at six three zero pee em."),
        ("He scored 98.6.", "He scored ninety-eight point six.", "He scored nine eight six."),
        ("Use HDMI 2.1.", "Use H D M I two point one.", "Use hdmi twenty one."),
        ("The file is in PDF.", "The file is in P D F.", "The file is in pudf."),
        ("This runs on iOS 18.", "This runs on eye oh ess eighteen.", "This runs on ios one eight."),
        ("The engine is V8.", "The engine is V eight.", "The engine is vee eight."),
        ("He paid EUR 40.", "He paid forty euros.", "He paid E U R forty."),
        ("The code is XJ-9.", "The code is X J nine.", "The code is exjay dash nine."),
        ("We met on Route 66.", "We met on Route sixty-six.", "We met on Route six six."),
        ("The box is 28cm wide.", "The box is twenty-eight centimeters wide.", "The box is two eight C M wide."),
        ("It supports Wi-Fi 6.", "It supports Wi-Fi six.", "It supports wife eye six."),
    ]
    rows: List[Dict[str, Any]] = []
    for idx, (compact_text, spoken_safe, wrong_reading) in enumerate(specs, start=1):
        options, label = _shuffle_options(rng, correct_text=spoken_safe, distractors=[compact_text, wrong_reading])
        rows.append(
            {
                "id": f"tts_{idx:03d}",
                "capability": "tts_stability",
                "task": "Choose the best TTS-safe rewrite. Keep the meaning, but prefer the option that is easiest for an English TTS engine to pronounce correctly.",
                "options": options,
                "label": label,
            }
        )
    return rows


def build_suite(*, seed: int = 42) -> List[Dict[str, Any]]:
    rng = random.Random(int(seed))
    rows: List[Dict[str, Any]] = []
    rows.extend(_build_terminology_rows(rng))
    rows.extend(_build_readability_rows(rng))
    rows.extend(_build_tts_rows(rng))
    return rows


def _write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    count = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build a medium-size lite capability evaluation suite.")
    p.add_argument(
        "--out",
        type=Path,
        default=Path("eval/lite_smallmodel_capabilities/capability_suite_medium.jsonl"),
        help="Output JSONL path",
    )
    p.add_argument("--seed", type=int, default=42, help="Deterministic seed for option shuffling")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    rows = build_suite(seed=int(args.seed))
    n = _write_jsonl(Path(args.out), rows)
    print(json.dumps({"output": str(args.out), "items": n, "seed": int(args.seed)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
