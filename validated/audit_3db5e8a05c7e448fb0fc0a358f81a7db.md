Let me analyze the external bug's core invariant and search for analogs in nearcore.

The external bug: a cumulative value meant to represent "end of period P" gets set to "start of period P+2" when period P+1 is skipped, causing inflated reward calculations.

The nearcore analog to search for: epoch-based cumulative values (rewards, stake, protocol versions) that could be miscalculated when epochs are skipped.