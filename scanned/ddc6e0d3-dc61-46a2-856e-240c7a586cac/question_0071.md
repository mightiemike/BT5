# Q71: BTC mainnet batch-split retarget transition

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Bitcoin mainnet fork after first storing the attacker fork as non-canonical and then extending it until it barely outruns the honest tip, where the attacker can split the critical headers across two relayer batches and verify that the second batch cannot validate against stale predecessor assumptions from the first, so that the contract stores a fork that the source chain would reject as canonical and a downstream bridge treats invalid confirmations as final?

## Target
- File/function: contract/src/bitcoin.rs::get_next_work_required + relayer/src/main.rs::prepare_and_submit_batches + relayer/src/near_client.rs::sign_submit_blocks
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Bitcoin header fork with crafted `prev_block_hash`, `bits`, `time`, `version`, and fork order that the default relayer can observe and forward
- Exploit idea: split the critical headers across two relayer batches and verify that the second batch cannot validate against stale predecessor assumptions from the first
- Invariant to test: splitting a fork across relayer batches must not change whether the headers are valid or canonical
- Expected Immunefi impact: Contract execution flows
- Fast validation: Replay the same fork once in one batch and once split across two batches and compare acceptance and tip state.
