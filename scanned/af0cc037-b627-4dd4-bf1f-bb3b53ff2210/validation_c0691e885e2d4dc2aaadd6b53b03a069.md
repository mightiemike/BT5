### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Enabling Allowlist Bypass - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. A pool admin who allowlists the router to support router-mediated swaps for their curated users simultaneously opens the pool to every user who calls the router, defeating the allowlist entirely.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct key for the mapping) and `sender` is the first argument forwarded by the pool from its own `msg.sender`. `ExtensionCalling._beforeSwap` passes `sender` directly from the pool's call context:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)
)
``` [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls the pool, the router is `msg.sender` to the pool:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [3](#0-2) 

So the extension receives `sender = address(router)`, not the end user's address. The allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`.

The `FullMetricExtensionTest` confirms this binding: the test allowlists `callers[0]` (the `TestCaller` wrapper contract, i.e., the immediate pool caller), not `users[0]` (the human address):

```solidity
swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);
_swap(0, users[0], false, int128(1000), type(uint128).max);
``` [4](#0-3) 

---

### Impact Explanation

A pool admin who deploys a curated pool (e.g., KYC-only, institution-only) with `SwapAllowlistExtension` and wants to support the official router for their allowlisted users must allowlist the router address. Once the router is in `allowedSwapper[pool]`, **every user** who calls any of the router's public entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) passes the allowlist check, because the extension only sees the router address. Non-allowlisted users can freely trade on the curated pool, causing direct curation failure and potential loss of LP principal if the pool was designed to restrict counterparties.

---

### Likelihood Explanation

The scenario is predictable and requires no privileged or malicious setup:

1. Pool admin creates a curated pool with `SwapAllowlistExtension`.
2. Admin allowlists specific user addresses and also allowlists the router so those users can use the standard periphery.
3. Any unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool.
4. The router is allowlisted → swap succeeds.

Step 2 is a natural, expected admin action. The router is a supported, public periphery contract. No malicious token, no privileged attacker role, no non-standard ERC20 required.

---

### Recommendation

The router must forward the original caller's identity to the pool so the extension can gate the correct actor. One approach: add a `sender` parameter to the router's swap functions and pass it as the first argument to `pool.swap`, or use a transient-storage pattern (analogous to how the router already stores the payer in `_setNextCallbackContext`) to communicate the originating user to the extension. The extension would then read the true end-user address rather than the router address.

Alternatively, document that the `SwapAllowlistExtension` gates the immediate pool caller only, and that router-mediated swaps require a separate per-user allowlist mechanism at the router layer.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension.
2. Admin calls setAllowedToSwap(pool, router, true)   // to support router users
3. Admin does NOT call setAllowedToSwap(pool, attacker, true)
4. Attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
   → router calls pool.swap(recipient, ...)
   → pool calls _beforeSwap(router, ...)
   → SwapAllowlistExtension checks allowedSwapper[pool][router] == true
   → swap succeeds
5. Attacker has traded on a pool they were never individually allowlisted for.
``` [5](#0-4) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L69-73)
```text
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
```
