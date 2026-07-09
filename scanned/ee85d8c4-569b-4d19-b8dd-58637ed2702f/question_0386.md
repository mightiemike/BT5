# Q386: LTC mainnet heavier-by-one work promotion

## Question
Can an unprivileged attacker cause the production relayer flow to submit a crafted Litecoin mainnet fork after relayer recovery from `PrevBlockNotFound` resumes at the last common ancestor, where the attacker can make a malicious fork outrun the honest tip by the smallest possible claimed work margin and test whether rounding or work accounting lets an under-secured fork win, so that the contract stores a fork that the source chain would reject as canonical and a downstream bridge treats invalid confirmations as final?

## Target
- File/function: contract/src/lib.rs::submit_block_header_inner + btc-types/src/utils.rs::work_from_bits
- Entrypoint: relayer-mediated `submit_blocks` through `Synchronizer::sync -> NearClient::sign_submit_blocks -> BtcLightClient::submit_blocks`
- Attacker controls: an attacker-controlled Litecoin fork with scrypt-valid headers, crafted `bits`, `time`, and fork order around difficulty boundaries
- Exploit idea: make a malicious fork outrun the honest tip by the smallest possible claimed work margin and test whether rounding or work accounting lets an under-secured fork win
- Invariant to test: a fork must only be promoted when its cumulative work truly exceeds the honest chain under consensus accounting
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Construct nearly equal-work forks and assert the contract never promotes the weaker branch.
