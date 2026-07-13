### Title
Last-Minute Tier Position Creation Manipulates Governance Tally Without Long-Term Commitment — (File: x/tieredrewards/keeper/voting_power.go)

---

### Summary

The custom governance tally function reads tier position voting power from **live delegation state at tally time**, with no snapshot, no minimum position age, and no exclusion for positions already in exit mode. An attacker can create a large tier position with `TriggerExitImmediately: true` in the block before a governance proposal's voting period ends, cast a decisive vote, and recover their tokens after the tier's `ExitDuration` elapses — without ever being a long-term participant in the system.

---

### Finding Description

`positionVotingPower` in `voting_power.go` is the sole gate for whether a tier position contributes governance power: [1](#0-0) 

The only check is `pos.IsDelegated()`. There is no check on:
- `pos.CreatedAtTime` — position age relative to the proposal
- `pos.HasTriggeredExit()` — whether the position is already committed to leaving
- Any minimum lock duration before governance participation

The custom tally function `NewCustomTallyTierVotesFn` calls `GetPositionStatesByOwner` at tally time (end of voting period), which returns all current positions including ones created in the same block: [2](#0-1) 

The `MsgLockTier` message accepts a `TriggerExitImmediately: true` flag, which starts the exit clock at creation time: [3](#0-2) [4](#0-3) 

This means a position created with `TriggerExitImmediately: true` is already in exit mode from block 1, yet ADR-006 §8.5 explicitly states — and the code confirms — that exiting-but-delegated positions still contribute full voting power: [5](#0-4) 

The attacker's entry path is a standard, unprivileged `MsgLockTier` transaction followed by a `MsgVote` transaction, both available to any account: [6](#0-5) 

---

### Impact Explanation

An attacker who can observe the live vote tally (all votes are public on-chain) can calculate the exact token amount needed to swing the outcome. They create a tier position of that size with `TriggerExitImmediately: true` just before the voting period ends, vote, and recover their tokens after `ExitDuration` elapses. The corrupted value is the **governance vote result** — the `results` map in `NewCustomTallyTierVotesFn` is inflated by the attacker's freshly-created position shares, potentially reversing the outcome of any proposal (parameter changes, treasury spending, tier management, etc.).

The attacker's only cost is the opportunity cost of locking tokens for `ExitDuration`. For tiers with short exit durations, or for governance proposals with high economic value, this cost is acceptable.

---

### Likelihood Explanation

**Medium.** The attack requires capital proportional to the existing vote tally, which is observable on-chain. The attacker must hold tokens for `ExitDuration` (a known, bounded cost). The attack is fully permissionless — no privileged role, no leaked key, no social engineering. The entry path (`MsgLockTier` + `MsgVote`) is a standard production transaction flow. The attack becomes more feasible as governance proposals with high economic value are submitted, or when tiers with shorter exit durations exist.

---

### Recommendation

1. **Snapshot voting power at proposal submission time** (or at a fixed block before the voting period ends) rather than reading live delegation state at tally time. This is the standard mitigation for this class of attack.
2. **Exclude positions in exit mode** (`HasTriggeredExit()`) from governance voting power. A position that has already committed to leaving the system should not influence governance outcomes.
3. **Require a minimum position age** relative to the proposal's submission block (`CreatedAtHeight <= proposal.SubmitBlock`) before a position can contribute to governance voting power.

---

### Proof of Concept

1. Governance proposal P is active; voting ends at block T.
2. Attacker observes the live tally: Alice and Bob have voted Yes/No with 10,000 tokens each.
3. At block T−1, attacker submits:
   ```
   MsgLockTier{Owner: Eve, Id: 1, Amount: 20001, ValidatorAddress: V, TriggerExitImmediately: true}
   ```
4. At block T−1, attacker submits `MsgVote{ProposalId: P, Option: No}`.
5. At block T, `NewCustomTallyTierVotesFn` runs. `GetPositionStatesByOwner(Eve)` returns the freshly-created position. `positionVotingPower` returns 20001 (delegated, bonded validator). Eve's No vote wins.
6. After `ExitDuration` elapses, attacker calls `MsgExitTierWithDelegation` or `MsgTierUndelegate` + `MsgWithdrawFromTier` to recover all 20001 tokens.

The broken invariant: tier positions are designed to represent long-term locked stake, but `positionVotingPower` treats a position created moments before the tally identically to one locked for years. [1](#0-0) [2](#0-1) [7](#0-6)

### Citations

**File:** x/tieredrewards/keeper/voting_power.go (L15-27)
```go
func positionVotingPower(
	pos types.PositionState,
	bondedVals map[string]v1.ValidatorGovInfo,
) math.LegacyDec {
	if !pos.IsDelegated() {
		return math.LegacyZeroDec()
	}
	val, ok := bondedVals[pos.Delegation.ValidatorAddress]
	if !ok || val.DelegatorShares.IsZero() {
		return math.LegacyZeroDec()
	}
	return pos.Delegation.Shares.MulInt(val.BondedTokens).Quo(val.DelegatorShares)
}
```

**File:** x/tieredrewards/keeper/gov_tally.go (L107-127)
```go
			positions, err := tierKeeper.GetPositionStatesByOwner(ctx, voter)
			if err != nil {
				return false, fmt.Errorf("error getting tier positions for %s: %w", vote.Voter, err)
			}
			for _, pos := range positions {
				posPower := positionVotingPower(pos, validators)
				if posPower.IsZero() {
					continue
				}

				valAddr := pos.Delegation.ValidatorAddress
				if val, ok := validators[valAddr]; ok {
					val.DelegatorDeductions = val.DelegatorDeductions.Add(pos.Delegation.Shares)
					validators[valAddr] = val
				}

				if err := distributeVotingPower(vote.Options, posPower, results); err != nil {
					return false, fmt.Errorf("invalid vote weight for voter %s: %w", vote.Voter, err)
				}
				totalVotingPower = totalVotingPower.Add(posPower)
			}
```

**File:** x/tieredrewards/keeper/msg_server.go (L24-86)
```go
func (ms msgServer) LockTier(ctx context.Context, msg *types.MsgLockTier) (*types.MsgLockTierResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	tier, err := ms.getTier(ctx, msg.Id)
	if err != nil {
		return nil, err
	}

	if err := ms.validateNewPosition(ctx, msg.Owner, msg.Amount, tier); err != nil {
		return nil, err
	}

	valAddr, err := sdk.ValAddressFromBech32(msg.ValidatorAddress)
	if err != nil {
		return nil, err
	}

	ownerAddr, err := sdk.AccAddressFromBech32(msg.Owner)
	if err != nil {
		return nil, err
	}

	id, err := ms.NextPositionId.Peek(ctx)
	if err != nil {
		return nil, err
	}
	delAddr, err := ms.createPositionDelegatorAccount(ctx, ownerAddr, id)
	if err != nil {
		return nil, err
	}

	if err := ms.lockFunds(ctx, ownerAddr, delAddr, msg.Amount); err != nil {
		return nil, err
	}

	if _, err := ms.delegate(ctx, delAddr, valAddr, msg.Amount); err != nil {
		return nil, err
	}

	pos, err := ms.createDelegatedPosition(ctx, msg.Owner, tier, valAddr, delAddr, msg.TriggerExitImmediately)
	if err != nil {
		return nil, err
	}

	// Defensive, but should not happen since transactions are sequential
	if pos.Id != id {
		return nil, errorsmod.Wrapf(types.ErrInvalidPositionID, "position id mismatch: peeked %d, created %d", id, pos.Id)
	}

	if err := ms.setPosition(ctx, pos, &ValidatorTransition{PreviousAddress: ""}); err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if err := sdkCtx.EventManager().EmitTypedEvent(&types.EventPositionCreated{
		Position: pos,
	}); err != nil {
		return nil, err
	}

	return &types.MsgLockTierResponse{PositionId: pos.Id}, nil
```

**File:** x/tieredrewards/types/position.go (L71-74)
```go
func (p *Position) TriggerExit(blockTime time.Time, duration time.Duration) {
	p.ExitTriggeredAt = blockTime
	p.ExitUnlockAt = blockTime.Add(duration)
}
```

**File:** x/tieredrewards/keeper/gov_tally_test.go (L632-659)
```go
// TestCustomTally_ExitingTierPositionIncluded verifies that a tier position
// with a triggered exit still contributes voting power.
func (s *KeeperSuite) TestCustomTally_ExitingTierPositionIncluded() {
	pos := s.setupNewTierPosition(sdkmath.NewInt(5000), true)
	delAddr := sdk.MustAccAddressFromBech32(pos.Owner)

	// Verify position state: delegated but exiting.
	allPositions, err := s.keeper.GetPositionStatesByOwner(s.ctx, delAddr)
	s.Require().NoError(err)
	s.Require().Len(allPositions, 1)
	s.Require().True(allPositions[0].IsDelegated(), "position should still be delegated")
	s.Require().True(allPositions[0].HasTriggeredExit(), "position should have triggered exit")

	s.insertVote(testProposalID, delAddr, yesVoteOpts())
	validators := s.buildValidatorsMap()

	// Per ADR-006 §8.5, exiting-but-delegated positions still count for
	// governance voting power.
	expectedPower := s.tierPowerFor(delAddr, validators)
	s.Require().True(expectedPower.IsPositive(),
		"exiting but delegated position should have positive power")

	totalPower, results := s.callCustomTally(testProposalID, validators)

	s.Require().True(totalPower.Equal(expectedPower),
		"exiting tier should contribute voting power; got %s, want %s", totalPower, expectedPower)
	s.Require().True(results[v1.OptionYes].Equal(expectedPower),
		"Yes should include exiting tier position; got %s", results[v1.OptionYes])
```

**File:** x/tieredrewards/client/cli/tx.go (L221-253)
```go
func GetCmdLockTier() *cobra.Command {
	cmd := newTxCmd(
		"lock-tier [id] [amount] [validator-address]",
		cobra.ExactArgs(3),
		"Lock tokens into a tier and delegate to a validator",
		func(clientCtx client.Context, cmd *cobra.Command, args []string) error {
			tierID, err := parseUint32Arg("id", args[0])
			if err != nil {
				return err
			}

			amount, err := parseMathIntArg("amount", args[1])
			if err != nil {
				return err
			}

			triggerExitImmediately, err := cmd.Flags().GetBool(flagTriggerExitImmediately)
			if err != nil {
				return err
			}

			return broadcastValidatedMsg(clientCtx, cmd, &types.MsgLockTier{
				Owner:                  clientCtx.GetFromAddress().String(),
				Id:                     tierID,
				Amount:                 amount,
				ValidatorAddress:       args[2],
				TriggerExitImmediately: triggerExitImmediately,
			})
		},
	)

	cmd.Flags().Bool(flagTriggerExitImmediately, false, "Start the exit commitment immediately after lock")
	return cmd
```
