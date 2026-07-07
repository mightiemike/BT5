### Title
`DirectDepositV1#creditDeposit` Can Be Called by Anyone, Enabling Front-Running of Owner's Emergency Withdrawal — (`core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` is an `external` function with no access control. Any unprivileged caller can invoke it at any time to force-deposit all protocol-recognized tokens held by the DDA contract into the hardcoded `subaccount`. This allows an attacker to front-run the DDA owner's `withdraw()` call, irrevocably moving tokens into the on-chain protocol and forcing the owner to wait through the `SLOW_MODE_TX_DELAY` (3 days) to recover them.

---

### Finding Description

`DirectDepositV1` is a deposit-address contract that holds tokens on behalf of a `subaccount`. It exposes two asset-movement paths:

- `creditDeposit()` — `external`, no modifier, callable by anyone. Iterates all spot product IDs, approves the `endpoint`, and calls `endpoint.depositCollateralWithReferral(subaccount, ...)` for every non-zero balance.
- `withdraw(token)` — `external onlyOwner`. Transfers the token balance directly to the owner. [1](#0-0) 

Because `creditDeposit()` carries no `onlyOwner` or equivalent guard, any externally-owned account can call it at any time. Once called, the tokens leave the DDA and enter the Endpoint/Clearinghouse via `depositCollateralWithReferral`. After that point, the owner cannot use `withdraw()` to recover them directly — the only recovery path is a slow-mode `WithdrawCollateral` transaction, which is subject to a hardcoded 3-day delay. [2](#0-1) 

The `withdraw()` function, which is the owner's direct-recovery escape hatch, simply reads `token.balanceOf(address(this))` and transfers it. If `creditDeposit()` has already drained the balance, `withdraw()` silently transfers zero. [3](#0-2) 

---

### Impact Explanation

An attacker who front-runs the owner's `withdraw()` transaction forces all protocol-recognized tokens held by the DDA into the on-chain subaccount. The owner loses immediate access to those tokens and must submit a slow-mode withdrawal and wait 3 days to recover them. In an emergency scenario (e.g., a protocol exploit is in progress, a product is being delisted, or the subaccount is compromised), this 3-day lock can result in meaningful asset loss or inability to act in time. The impact is **forced 3-day asset lock** for any token balance held by the DDA at the time of the attack.

---

### Likelihood Explanation

The attack requires only:
1. Monitoring the mempool for a `withdraw()` call from a DDA owner.
2. Submitting a `creditDeposit()` call with higher gas to front-run it.

No privileged access, no special tokens, and no protocol knowledge beyond the DDA's address are required. Any externally-owned account can execute this. The attack is realistic on any EVM chain with a public mempool, including Ink Chain.

---

### Recommendation

Add an `onlyOwner` modifier to `creditDeposit()`, or alternatively restrict it to a whitelist of trusted callers (e.g., the owner or a designated keeper). This mirrors the fix applied to the Teller analog: restrict the unguarded public function to the authorized party only.

```solidity
// Before
function creditDeposit() external {

// After
function creditDeposit() external onlyOwner {
``` [4](#0-3) 

---

### Proof of Concept

1. DDA owner deploys `DirectDepositV1` with their `subaccount` and receives 10,000 USDC into the DDA address.
2. Owner decides to recover the USDC directly (e.g., emergency) and submits `withdraw(USDC)`.
3. Attacker observes the pending `withdraw()` in the mempool and front-runs it with `creditDeposit()`.
4. `creditDeposit()` executes first: it calls `token.approve(endpoint, 10000e6)` then `endpoint.depositCollateralWithReferral(subaccount, USDC_PRODUCT_ID, 10000e6, "-1")`.
5. The Endpoint enqueues a `DepositCollateral` slow-mode transaction with `executableAt = block.timestamp + SLOW_MODE_TX_DELAY` (3 days). [2](#0-1) 

6. Owner's `withdraw()` now executes: `token.balanceOf(address(this))` returns 0, transferring nothing.
7. Owner must now submit a slow-mode `WithdrawCollateral` transaction and wait 3 days to recover the USDC. [3](#0-2)

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

**File:** core/contracts/Endpoint.sol (L152-153)
```text
        slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
```
