Looking at the external bug class: a guard function uses an indirect identifier (batch index) to gate an action, but the actual content at that identifier can differ from what the caller intended — leading to the wrong entity being gated and fund loss.

Let me trace the exact analog in the Metric OMM codebase.