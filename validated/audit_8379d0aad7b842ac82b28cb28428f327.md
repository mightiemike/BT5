### Title
Fee-free `depositCollateralWithReferral` enables risk-free slow mode queue flooding, delaying legitimate user withdrawals — (File: `core/contracts/Endpoint.sol`)

---

### Summary

The `depositCollateralWithReferral` function in `Endpoint.sol` enqueues slow mode transactions directly without charging the `SLOW_MODE_FEE`. All other user-initiated slow mode paths charge a non-recoverable $1 fee as a spam deterrent. Because the deposited capital is fully recoverable after the 3-day delay, an attacker can flood the unbounded FIFO slow mode queue at near-zero net cost, indefinitely delaying legitimate users' slow mode withdrawals and other queued operations — most critically during sequencer downtime, which is the exact scenario where the slow mode path is the only available exit.

---

### Finding Description

The slow mode queue is the protocol's censorship-resistance fallback. When the sequencer is offline, users call `executeSlowModeTransaction()` directly to process their own queued transactions. The queue is strictly FIFO: `txUpTo` increments sequentially, so any transaction enqueued after an attacker's batch cannot be reached until all prior entries are consumed.

**Two-tier cost asymmetry:**

`submitSlowModeTransactionImpl` in `EndpointTx.sol` charges `SLOW_MODE_FEE = $1` (non-recoverable) for every user-initiated slow mode transaction except deposits:

```solidity
// EndpointTx.sol:343-372
if (txType == IEndpoint.TransactionType.DepositCollateral) {
    revert();                          // deposit via this path is blocked
} else if (...owner-only types...) {
    require(sender == owner());
} else {
    chargeSlowModeFee(_getQuote(), sender);   // $1 non-recoverable fee
    slowModeFees += SLOW_MODE_FEE;
}
``` [1](#0-0) 

The deposit path in `Endpoint.sol` bypasses this entirely. It transfers tokens, then enqueues a `SlowModeTx` with **no fee**:

```solidity
// Endpoint.sol:144-166
handleDepositTransfer(...);
SlowModeConfig memory _slowModeConfig = slowModeConfig;
slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
    executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY,
    sender: sender,
    tx: abi.encodePacked(...)
});
slowModeConfig = _slowModeConfig;
``` [2](#0-1) 

The minimum deposit for an existing subaccount is `MIN_DEPOSIT_AMOUNT = $0.1`: [3](#0-2) 

After the 3-day `SLOW_MODE_TX_DELAY`, the deposit is credited back to the attacker's subaccount. The attacker then withdraws (paying `withdrawFeeX18`, which is a small per-product fee), and repeats. The net cost per queue slot is gas plus the withdrawal fee — not the $1 non-recoverable fee that protects all other slow mode paths. [4](#0-3) 

The queue storage is an unbounded `mapping(uint64 => SlowModeTx)` with a `uint64` counter — no cap exists: [5](#0-4) 

`executeSlowModeTransaction()` processes exactly one entry per call, in FIFO order: [6](#0-5) 

---

### Impact Explanation

An attacker with $100 of quote token can enqueue ~1,000 deposit slow mode transactions (at $0.1 each). All legitimate users' slow mode transactions submitted after the attack — withdrawals, `LinkSigner` changes, insurance deposits — are blocked behind the attacker's batch. Each entry must be individually popped via `executeSlowModeTransaction()` before the next entry is reachable.

During normal sequencer operation the sequencer clears the queue automatically, so the impact is limited. However, the slow mode path exists precisely for sequencer downtime. During an outage, users must call `executeSlowModeTransaction()` themselves. With 1,000 attacker deposits ahead of a user's withdrawal, the user must submit 1,000 on-chain transactions (each costing gas) before their own withdrawal is reachable. An attacker with $10,000 can scale this to 100,000 entries. The capital is fully recovered after each 3-day cycle, making this a **repeatable, near-zero-net-cost liveness attack on the only censorship-resistant exit path**.

The broken invariant: the slow mode queue is supposed to guarantee timely fund access independent of sequencer liveness. The fee-free deposit enqueue path destroys this guarantee.

---

### Likelihood Explanation

**Medium.** The attack requires holding the minimum deposit amount in quote tokens and paying gas per enqueue. The capital is recoverable, so the sustained cost is only gas plus withdrawal fees. Any actor motivated to delay a specific user's withdrawal (e.g., to prevent them from closing a position before liquidation, or to grief a competitor) has a concrete, low-cost mechanism. The attack is most impactful precisely when the sequencer is offline — the scenario the slow mode path is designed to handle.

---

### Recommendation

1. **Charge a non-recoverable slow mode fee for deposit enqueues.** Deduct `SLOW_MODE_FEE` from the deposited amount before crediting the subaccount, or require a separate fee token transfer alongside the deposit. This aligns the deposit path's spam cost with all other slow mode paths.
2. **Alternatively, impose a per-address rate limit** on the number of pending slow mode deposit entries (e.g., max 1 pending deposit per subaccount at a time), enforced at enqueue time.
3. **Document the queue-flooding risk** in the guarded-launch security model so operators can monitor `slowModeConfig.txCount - slowModeConfig.txUpTo` as a liveness health signal.

---

### Proof of Concept

```
Attacker setup:
  - Holds 1,000 USDC (quote token)
  - Has an existing subaccount (MIN_DEPOSIT_AMOUNT = $0.1 applies)

Step 1: Call Endpoint.depositCollateral(subaccountName, QUOTE_PRODUCT_ID, 0.1e6)
        → tokens transferred to clearinghouse
        → slowModeTxs[txCount] = SlowModeTx{executableAt: now+3days, ...}
        → txCount++
        → NO SLOW_MODE_FEE charged

Step 2: Repeat 9,999 more times across multiple blocks.
        → 10,000 deposit entries now occupy slots [N, N+9999] in the queue.
        → Cost: gas only (capital locked for 3 days, then recoverable).

Step 3: Victim calls Endpoint.submitSlowModeTransaction(WithdrawCollateral tx)
        → pays $1 SLOW_MODE_FEE
        → enqueued at slot N+10000

Step 4: Sequencer goes offline.

Step 5: Victim calls executeSlowModeTransaction() to self-serve their withdrawal.
        → processes slot N (attacker deposit #1) — victim's withdrawal still unreachable.
        → Victim must call this 10,000 more times before slot N+10000 is reached.

Step 6: After 3 days, attacker's deposits are credited.
        Attacker withdraws, recovers capital, repeats the flood.
```

### Citations

**File:** core/contracts/EndpointTx.sol (L343-372)
```text
        if (txType == IEndpoint.TransactionType.DepositCollateral) {
            revert();
        } else if (txType == IEndpoint.TransactionType.DepositInsurance) {
            IEndpoint.DepositInsurance memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.DepositInsurance)
            );
            require(
                txn.amount >= uint128(SLOW_MODE_FEE),
                ERR_DEPOSIT_TOO_SMALL
            );
            handleDepositTransfer(_getQuote(), sender, uint256(txn.amount));
        } else if (
            txType == IEndpoint.TransactionType.WithdrawInsurance ||
            txType == IEndpoint.TransactionType.DelistProduct ||
            txType == IEndpoint.TransactionType.DumpFees ||
            txType == IEndpoint.TransactionType.RebalanceXWithdraw ||
            txType == IEndpoint.TransactionType.UpdateTierFeeRates ||
            txType == IEndpoint.TransactionType.AddNlpPool ||
            txType == IEndpoint.TransactionType.UpdateNlpPool ||
            txType == IEndpoint.TransactionType.DeleteNlpPool ||
            txType == IEndpoint.TransactionType.ForceRebalanceNlpPool ||
            txType == IEndpoint.TransactionType.NlpProfitShare ||
            txType == IEndpoint.TransactionType.UpdateBuilder
        ) {
            require(sender == owner());
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
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

**File:** core/contracts/Endpoint.sol (L231-236)
```text
    function executeSlowModeTransaction() external {
        SlowModeConfig memory _slowModeConfig = slowModeConfig;
        _executeSlowModeTransaction(_slowModeConfig, false);
        nSubmissions += 1;
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/common/Constants.sol (L40-42)
```text
int256 constant MIN_DEPOSIT_AMOUNT = ONE / 10; // $0.1

int256 constant MIN_FIRST_DEPOSIT_AMOUNT = 5 * ONE; // $5
```

**File:** core/contracts/common/Constants.sol (L50-50)
```text
uint64 constant SLOW_MODE_TX_DELAY = 3 * 24 * 60 * 60; // 3 days
```

**File:** core/contracts/EndpointStorage.sol (L38-39)
```text
    IEndpoint.SlowModeConfig internal slowModeConfig;
    mapping(uint64 => IEndpoint.SlowModeTx) internal slowModeTxs;
```
