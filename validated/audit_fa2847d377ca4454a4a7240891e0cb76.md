### Title
`depositCollateralWithReferral` Bypasses Slow Mode Fee, Enabling Near-Zero-Cost `slowModeTxs` Queue Bloat — (`core/contracts/Endpoint.sol`)

---

### Summary

`depositCollateralWithReferral` in `Endpoint.sol` writes entries into the `slowModeTxs` queue without charging the `SLOW_MODE_FEE`. The only barrier is a `MIN_DEPOSIT_AMOUNT` of `$0.1`, which is credited back to the attacker as collateral — not consumed as a fee. Every other user-accessible slow mode path charges a non-refundable `$1` fee. An attacker with an existing subaccount can flood the queue at a cost of only L2 gas per entry, bloating the `slowModeTxs` mapping and forcing the sequencer to drain the queue before legitimate user withdrawals can be processed.

---

### Finding Description

The protocol defines two distinct paths for adding entries to the `slowModeTxs` queue:

**Path 1 — `submitSlowModeTransactionImpl` (fee-gated):**
In `EndpointTx.sol`, the `else` branch at line 370 calls `chargeSlowModeFee`, which pulls `clearinghouse.getSlowModeFee()` (= `SLOW_MODE_FEE * decimals_multiplier`, i.e. `$1` in quote token) from the caller's wallet as a non-refundable fee before writing to `slowModeTxs`. [1](#0-0) 

**Path 2 — `depositCollateralWithReferral` (fee-free):**
In `Endpoint.sol`, this function transfers the deposit amount to the clearinghouse and then directly writes a `SlowModeTx` entry. There is no call to `chargeSlowModeFee` anywhere in this path. [2](#0-1) 

The only guard is `isValidDepositAmount`, which for an **existing** subaccount requires only `MIN_DEPOSIT_AMOUNT = ONE / 10 = $0.1`: [3](#0-2) 

Critically, the `$0.1` is not a fee — it is transferred to the clearinghouse as collateral and credited to the attacker's subaccount. The attacker recovers it via the sequencer's fast-path withdrawal. The net cost per queue entry is therefore **only L2 gas**.

The `slowModeTxs` mapping is unbounded: [4](#0-3) 

Entries are processed strictly in FIFO order via `_executeSlowModeTransaction`, which reads `slowModeTxs[_slowModeConfig.txUpTo]` and increments the pointer: [5](#0-4) 

---

### Impact Explanation

An attacker floods the `slowModeTxs` queue with `DepositCollateral` entries. The sequencer must drain these entries in order before it can reach any legitimate user slow mode transaction (e.g., `WithdrawCollateral`, `LinkSigner`). Users who submitted slow mode withdrawals — the censorship-resistance fallback for fund recovery — face unbounded delay proportional to the number of attacker entries. The `SLOW_MODE_TX_DELAY` is already 3 days; a bloated queue compounds this. The attacker's capital is fully recovered via fast-path withdrawal, making the attack economically self-sustaining.

---

### Likelihood Explanation

The entry point is the public `depositCollateral` function, callable by any non-sanctioned address with an existing subaccount (minimum first deposit `$5`). After the subaccount is established, each subsequent queue entry costs only L2 gas. On Ink Chain (an L2), gas costs are negligible. The attack requires no privileged access, no oracle manipulation, and no protocol-specific knowledge beyond the public ABI. [6](#0-5) 

---

### Recommendation

Charge the slow mode fee inside `depositCollateralWithReferral` before writing to `slowModeTxs`, consistent with all other user-accessible slow mode paths:

```solidity
// In depositCollateralWithReferral, before writing to slowModeTxs:
chargeSlowModeFee(_getQuote(), msg.sender);
slowModeFees += SLOW_MODE_FEE;
```

Alternatively, enforce a meaningful minimum deposit value that is not recoverable (i.e., treat a portion as a non-refundable queue-entry fee), or rate-limit the number of pending slow mode entries per subaccount.

---

### Proof of Concept

1. Attacker calls `depositCollateral(subaccountName, QUOTE_PRODUCT_ID, minAmount)` with `minAmount` satisfying `MIN_DEPOSIT_AMOUNT = $0.1` for an existing subaccount.
2. `depositCollateralWithReferral` transfers `$0.1` to the clearinghouse and appends a `SlowModeTx` entry to `slowModeTxs[txCount++]` — no slow mode fee is charged.
3. The sequencer processes the deposit (fast path), crediting `$0.1` to the attacker's subaccount.
4. Attacker withdraws `$0.1` via the sequencer's fast-path `WithdrawCollateral` (no slow mode fee required for sequencer-submitted transactions).
5. Attacker repeats steps 1–4 N times. Each iteration costs only L2 gas (~fractions of a cent on Ink Chain).
6. The `slowModeTxs` queue now contains N attacker entries ahead of any legitimate user withdrawal. The sequencer must process all N entries before reaching the user's transaction, delaying fund recovery by an attacker-controlled duration. [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** core/contracts/EndpointTx.sol (L369-384)
```text
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }

        IEndpoint.SlowModeConfig memory _slowModeConfig = slowModeConfig;
        requireUnsanctioned(sender);
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
        // TODO: to save on costs we could potentially just emit something
        // for now, we can just create a separate loop in the engine that queries the remote
        // sequencer for slow mode transactions, and ignore the possibility of a reorgy attack
        slowModeConfig = _slowModeConfig;
```

**File:** core/contracts/Endpoint.sol (L103-121)
```text
    function depositCollateral(
        bytes12 subaccountName,
        uint32 productId,
        uint128 amount
    ) external {
        bytes32 subaccount = bytes32(
            abi.encodePacked(msg.sender, subaccountName)
        );
        require(
            isValidDepositAmount(subaccount, productId, amount),
            ERR_DEPOSIT_TOO_SMALL
        );
        depositCollateralWithReferral(
            subaccount,
            productId,
            amount,
            DEFAULT_REFERRAL_CODE
        );
    }
```

**File:** core/contracts/Endpoint.sol (L144-166)
```text
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
```

**File:** core/contracts/Endpoint.sol (L193-194)
```text
        SlowModeTx memory txn = slowModeTxs[_slowModeConfig.txUpTo];
        delete slowModeTxs[_slowModeConfig.txUpTo++];
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

**File:** core/contracts/EndpointStorage.sol (L38-39)
```text
    IEndpoint.SlowModeConfig internal slowModeConfig;
    mapping(uint64 => IEndpoint.SlowModeTx) internal slowModeTxs;
```
