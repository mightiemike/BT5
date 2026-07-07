### Title
Permissionless `creditDeposit()` Allows Any Caller to Force DDA Token Deposits Into Fixed Subaccount — (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` carries no access control modifier. Any external actor can invoke it at will to sweep all token balances held by the DDA contract into the hardcoded `subaccount`, bypassing the owner's ability to recover those tokens via the `withdraw()` path and forcing them through the protocol's 3-day slow-mode withdrawal queue instead.

---

### Finding Description

`creditDeposit()` is declared `external` with no `onlyOwner` or equivalent guard:

```solidity
function creditDeposit() external {
    uint32[] memory productIds = spotEngine.getProductIds();
    for (uint256 i = 0; i < productIds.length; i++) {
        uint32 productId = productIds[i];
        address tokenAddr = spotEngine.getToken(productId);
        require(tokenAddr != address(0), "Invalid productId.");
        IIERC20Base token = IIERC20Base(tokenAddr);
        uint256 balance = token.balanceOf(address(this));
        if (balance != 0) {
            token.approve(address(endpoint), balance);
            endpoint.depositCollateralWithReferral(
                subaccount,
                productId,
                uint128(balance),
                "-1"
            );
        }
    }
}
``` [1](#0-0) 

The function iterates over every product ID registered in the `SpotEngine`, approves the full balance of each token to the `Endpoint`, and calls `depositCollateralWithReferral()` targeting the immutable `subaccount` set at construction time. There is no caller check, no rate limit, and no fee.

By contrast, the only privileged recovery path — `withdraw()` — is correctly gated:

```solidity
function withdraw(IIERC20Base token) external onlyOwner {
``` [2](#0-1) 

The asymmetry is the root cause: the deposit path is open to everyone; the withdrawal path is restricted to the owner.

Each successful `depositCollateralWithReferral()` call inside `creditDeposit()` enqueues a new `SlowModeTx` in `Endpoint`:

```solidity
slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
    executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY,
    sender: sender,
    tx: abi.encodePacked(...)
});
``` [3](#0-2) 

`SLOW_MODE_TX_DELAY` is hardcoded to three days. Once tokens are swept into the slow-mode queue, the owner cannot recover them via `withdraw()` — they must wait for the sequencer to process the queued `DepositCollateral` transaction and then submit a separate withdrawal.

---

### Impact Explanation

**Impact: Medium**

1. **Forced asset lock-up.** An owner who holds tokens in the DDA intending to call `withdraw()` (e.g., to redirect funds, respond to an emergency, or cancel a planned deposit) can be front-run by any attacker calling `creditDeposit()`. The tokens are immediately swept into the protocol and locked behind the 3-day slow-mode delay. The owner cannot cancel or reverse the queued deposit.

2. **Slow-mode queue pollution.** Because `creditDeposit()` iterates over all registered product IDs, a caller can trigger one slow-mode enqueue per product that has a non-zero DDA balance. Repeated calls (interleaved with small token transfers to the DDA) bloat the slow-mode queue at zero cost to the attacker, increasing sequencer processing burden.

3. **No token theft, but loss of custody control.** Funds are not redirected to the attacker — they go to the fixed `subaccount`. However, the owner loses the ability to exercise the `withdraw()` escape hatch, which is the only direct recovery mechanism available without protocol interaction.

---

### Likelihood Explanation

**Likelihood: Medium**

- The DDA contract address is publicly discoverable on-chain.
- The call requires no tokens, no signature, and no privileged role — only gas.
- The attack is most impactful when the DDA holds a meaningful balance, which is the normal operating state of the contract (tokens accumulate between `creditDeposit()` calls).
- A griefing attacker monitoring the mempool can front-run any owner `withdraw()` attempt with a `creditDeposit()` call.

---

### Recommendation

Add `onlyOwner` to `creditDeposit()`, consistent with the access control already applied to `withdraw()` and `withdrawNative()`:

```solidity
function creditDeposit() external onlyOwner {
```

Alternatively, if permissionless calling is intentional (e.g., for keeper bots), introduce a time-lock or minimum-balance threshold to prevent zero-cost queue spam, and document the trust model explicitly.

---

### Proof of Concept

1. Owner deploys `DirectDepositV1` with `subaccount = ownerSubaccount`.
2. 1,000 USDC accumulates in the DDA from user transfers.
3. Owner decides to recover the USDC directly and calls `withdraw(USDC)`.
4. Attacker observes the pending `withdraw()` in the mempool and front-runs it with `creditDeposit()`.
5. `creditDeposit()` executes first: approves 1,000 USDC to `Endpoint`, calls `depositCollateralWithReferral(ownerSubaccount, productId, 1000e6, "-1")`.
6. A `SlowModeTx` is enqueued with `executableAt = block.timestamp + 3 days`.
7. Owner's `withdraw()` call now sees a zero balance and transfers nothing.
8. Owner must wait ≥3 days for the sequencer to process the deposit, then submit a separate `WithdrawCollateral` slow-mode transaction (paying the slow-mode fee and waiting another 3 days), to recover the funds. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L83-101)
```text
    function creditDeposit() external {
        uint32[] memory productIds = spotEngine.getProductIds();
        for (uint256 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            address tokenAddr = spotEngine.getToken(productId);
            require(tokenAddr != address(0), "Invalid productId.");
            IIERC20Base token = IIERC20Base(tokenAddr);
            uint256 balance = token.balanceOf(address(this));
            if (balance != 0) {
                token.approve(address(endpoint), balance);
                endpoint.depositCollateralWithReferral(
                    subaccount,
                    productId,
                    uint128(balance),
                    "-1"
                );
            }
        }
    }
```

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```

**File:** core/contracts/Endpoint.sol (L152-166)
```text
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
