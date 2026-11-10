# Labelled report titles

`inside_info_subtypes.jsonl`: 184 titles of inside-information reports published in 2015–2016,
drawn at random (stratified by the sub-type the rules gave them at the time, seed 20261110),
each labelled by hand from the title alone. Titles that name a natural person were left out.

The rules had been written against reports from October–November 2026 only, so on the first
run this was a held-out set: accuracy was **76.1 %**. The gaps it showed (loan and share
purchase agreements read as contracts, revenue reports, lawsuits worded as "powództwo") were
then fixed, which brought it to 93.5 %. From that point on it is a regression set, not a
measure of accuracy on unseen reports; a new sample is needed for that.

The test in `tests/unit/test_classify.py` fails if accuracy drops below 90 %.
