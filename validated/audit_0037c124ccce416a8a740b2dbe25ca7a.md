### Title
Unbounded Slow-Mode Queue Flooding via Cheap `depositCollateralWithReferral` Calls Blocks Legitimate Slow-Mode Withdrawals — (`File: core/contracts/Endpoint.sol`)

---

### Summary

`depositCollateralWithReferral` is a `public` function that unconditionally appends an entry to the FIFO `slowModeTxs` queue while only requiring a `MIN_DEPOSIT_AMOUNT` of $0.10. All other user-initiated slow-mode transactions pay a `SLOW_MODE_FEE` of $1.00. An attacker can exploit this 10× cost asymmetry to cheaply flood the queue, forcing the sequencer to drain thousands of spam entries before any legitimate slow-mode withdrawal queued behind them can execute.

---

### Finding Description

`Endpoint.depositCollateralWithReferral` is declared `public` and appends a `SlowModeTx` entry directly to `slowModeTxs` without charging the protocol's `SLOW_MODE_FEE`: [1](#0-0) 

The only economic barrier is the deposit amount itself, gated by `isValidDepositAmount`: [2](#0-1) 

`MIN_DEPOSIT_AMOUNT` is $0.10 for existing accounts: [3](#0-2) 

By contrast, every other user-initiated slow-mode transaction (withdrawals, `LinkSigner`, `ClaimBuilderFee`, etc.) must pay `SLOW_MODE_FEE = $1.00` via `chargeSlowModeFee`: [4](#0-3) [5](#0-4) 

The slow-mode queue is a FIFO structure backed by an unbounded `mapping(uint64 => SlowModeTx)`: [6](#0-5) 

Execution always advances `txUpTo` by exactly one per call: [7](#0-6) 

This means every spam entry inserted before a legitimate withdrawal must be individually drained before that withdrawal can execute.

**Attack path:**

1. Attacker calls `depositCollateralWithReferral(subaccount, productId, MIN_DEPOSIT_AMOUNT, "")` in a loop, paying only $0.10 per queue entry (plus gas). The deposited tokens are credited to the attacker's subaccount as collateral — the capital is not destroyed, only temporarily locked.
2. Each call appends one `SlowModeTx` with `executableAt = block.timestamp + 3 days`.
3. A legitimate user later calls `submitSlowModeTransaction` for a withdrawal, paying $1.00. Their entry lands at a high `txCount` index.
4. The sequencer must include one `ExecuteSlowMode` transaction per spam entry to drain the queue. Until all spam entries are processed, the legitimate withdrawal at the tail cannot execute.
5. If the sequencer is slow or uncooperative, the user calls `executeSlowModeTransaction()` themselves — but must pay gas for every spam entry ahead of theirs.

The attacker recovers their deposited collateral by later submitting a withdrawal (which itself joins the queue, but behind the spam they already inserted). The net cost to the attacker is gas only; the $0.10 deposits are fully recoverable.

---

### Impact Explanation

Legitimate slow-mode withdrawals are blocked until the sequencer or users drain all preceding spam entries. Because `executeSlowModeTransaction` processes exactly one entry per call, a queue flooded with N spam entries requires N sequential on-chain transactions before the victim's withdrawal can execute. This directly delays or effectively denies access to user funds via the censorship-resistance path — the one path that is supposed to be immune to sequencer censorship. The sequencer also bears increased operational cost processing spam `ExecuteSlowMode` batches.

**Corrupted state**: `slowModeConfig.txUpTo` is artificially held behind `txCount`, trapping legitimate `SlowModeTx` entries at high indices and preventing their execution.

---

### Likelihood Explanation

The entry point is `public` with no access control. The cost per spam entry is $0.10 in recoverable collateral plus gas. On a cheap EVM-compatible chain (Ink Chain, as referenced in the Nado deployment config), gas costs are negligible. An attacker can insert thousands of entries for a few dollars of gas, with full capital recovery. No privileged role, leaked key, or governance action is required.

---

### Recommendation

Apply the same `SLOW_MODE_FEE` charge inside `depositCollateralWithReferral` before appending to the queue, or restrict the function so that only the `DirectDepositV1` contract (or another trusted caller) can invoke it. Alternatively, enforce that the deposit amount must meet or exceed `SLOW_MODE_FEE` when the call originates from an unprivileged external caller, eliminating the cost asymmetry between deposit-based and fee-based slow-mode entries.

---

### Proof of Concept

```solidity
// Attacker script (pseudocode)
IERC20(quoteToken).approve(address(endpoint), type(uint256).max);

// Flood the queue with 10,000 entries at $0.10 each
for (uint i = 0; i < 10_000; i++) {
    endpoint.depositCollateralWithReferral(
        attackerSubaccount,
        QUOTE_PRODUCT_ID,
        uint128(MIN_DEPOSIT_AMOUNT), // $0.10
        ""
    );
}
// slowModeConfig.txCount is now 10,000 entries ahead of any legitimate tx

// Victim submits a withdrawal via slow mode (pays $1 SLOW_MODE_FEE)
endpoint.submitSlowModeTransaction(withdrawTx); // lands at index 10,000

// After 3 days, victim tries to execute their withdrawal:
// executeSlowModeTransaction() only advances txUpTo by 1.
// Victim must call it 10,000 times (or wait for sequencer) before their
// withdrawal at index 10,000 becomes reachable.
```

The attacker's $1,000 in deposited collateral is fully recoverable. The victim's withdrawal is blocked for as long as the spam queue is not drained. [8](#0-7) [9](#0-8)

### Citations

**File:** core/contracts/Endpoint.sol (L123-167)
```text
    function depositCollateralWithReferral(
        bytes32 subaccount,
        uint32 productId,
        uint128 amount,
        string memory
    ) public {
        require(!RiskHelper.isIsolatedSubaccount(subaccount), ERR_UNAUTHORIZED);

        address sender = address(bytes20(subaccount));

        // depositor / depositee need to be unsanctioned
        requireUnsanctioned(msg.sender);
        requireUnsanctioned(sender);

        if (!isValidDepositAmount(subaccount, productId, amount)) {
            // we cannot revert here, otherwise direct deposit could be blocked when there are
            // multiple assets awaiting credit but one of them is below the minimum deposit amount.
            // we can just skip the deposit and continue with the next asset.
            return;
        }

        handleDepositTransfer(
            IERC20Base(spotEngine.getToken(productId)),
            msg.sender,
            uint256(amount)
        );
        // copy from submitSlowModeTransaction
        SlowModeConfig memory _slowModeConfig = slowModeConfig;

        slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: abi.encodePacked(
                uint8(TransactionType.DepositCollateral),
                abi.encode(
                    DepositCollateral({
                        sender: subaccount,
                        productId: productId,
                        amount: amount
                    })
                )
            )
        });
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/Endpoint.sol (L193-194)
```text
        SlowModeTx memory txn = slowModeTxs[_slowModeConfig.txUpTo];
        delete slowModeTxs[_slowModeConfig.txUpTo++];
```

**File:** core/contracts/Endpoint.sol (L231-236)
```text
    function executeSlowModeTransaction() external {
        SlowModeConfig memory _slowModeConfig = slowModeConfig;
        _executeSlowModeTransaction(_slowModeConfig, false);
        nSubmissions += 1;
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/common/Constants.sol (L23-23)
```text
int128 constant SLOW_MODE_FEE = 1000000; // $1
```

**File:** core/contracts/common/Constants.sol (L40-42)
```text
int256 constant MIN_DEPOSIT_AMOUNT = ONE / 10; // $0.1

int256 constant MIN_FIRST_DEPOSIT_AMOUNT = 5 * ONE; // $5
```

**File:** core/contracts/EndpointTx.sol (L369-372)
```text
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }
```

**File:** core/contracts/EndpointStorage.sol (L38-39)
```text
    IEndpoint.SlowModeConfig internal slowModeConfig;
    mapping(uint64 => IEndpoint.SlowModeTx) internal slowModeTxs;
```
