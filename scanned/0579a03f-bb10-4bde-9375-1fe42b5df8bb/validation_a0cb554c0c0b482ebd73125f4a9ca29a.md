### Title
`SwapAllowlistExtension.beforeSwap` checks the router's address as `sender` instead of the actual end-user, allowing any user to bypass the per-user swap allowlist when interacting through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to gate pool swaps by individual swapper address. However, the pool passes `msg.sender` (the router) as the `sender` argument to the extension hook. The extension then checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`. If the pool admin allowlists the router to enable normal router-based usage, every user — including those the admin intended to block — can bypass the per-user allowlist entirely.

---

### Finding Description

The pool's `swap` function calls `_beforeSwap` with `msg.sender` as the `sender` argument: [1](#0-0) 

`_beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`. When a user routes through `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of the pool: [4](#0-3) 

So `sender` = router address, not the end user. The allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The contract's own NatSpec states it "Gates `swap` by swapper address, per pool": [5](#0-4) 

The implementation contradicts this intent whenever an intermediary router is in the call path.

---

### Impact Explanation

Two concrete failure modes arise:

**Mode A — Full allowlist bypass (fund-impacting):** The pool admin allowlists the router address so that normal users can swap through it. Because the check resolves to `allowedSwapper[pool][router] == true`, every user — including those the admin explicitly never allowlisted — passes the guard and executes swaps. The allowlist provides zero per-user access control. If the pool is meant to serve only KYC'd or whitelisted counterparties (a common use case for permissioned AMMs), any address can trade against pool liquidity, directly violating the LP's and admin's access-control invariant and exposing LPs to trades with unauthorized counterparties.

**Mode B — Permanent DoS of allowlisted users (broken core functionality):** If the admin allowlists individual user addresses (not the router), those users cannot swap through the router because the check resolves to `allowedSwapper[pool][router] == false`. The router is the primary user-facing interface; blocking it makes the pool's swap functionality unusable for all allowlisted users.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the standard user-facing swap interface for the protocol.
- Any pool that deploys `SwapAllowlistExtension` and expects per-user access control will be affected the moment a user routes through the router.
- No special privilege or unusual setup is required; the trigger is the ordinary swap path.
- The `onlyPoolAdmin` guard on `setAllowedToSwap` does not mitigate this — the admin correctly configures user addresses, but the hook reads the wrong address at runtime. [6](#0-5) 

---

### Recommendation

The pool must forward the true end-user identity to the extension. Two approaches:

1. **Pass the real user through `callbackData` / `extensionData`:** The router encodes `msg.sender` (the actual user) into `extensionData`; the extension decodes and verifies it. This requires the extension to trust the encoded value, which introduces its own risks unless the pool authenticates the data.

2. **Preferred — check `recipient` instead of `sender` for swap allowlisting, or add a dedicated `swapper` field to the hook interface:** The pool should pass the original initiator (e.g., recovered from a signed permit or from a trusted router registry) rather than the raw `msg.sender`. Alternatively, the extension can maintain a router→user mapping and require the router to register the user before the swap.

3. **Short-term mitigation:** Document that `SwapAllowlistExtension` only gates by direct caller (router/contract), not end user, and that per-user gating requires users to call the pool directly. Rename the mapping and NatSpec accordingly to avoid misuse.

---

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension in beforeSwap slot
  - Admin calls setAllowedToSwap(pool, router, true)   // allowlists the router
  - Admin never calls setAllowedToSwap(pool, alice, true)  // alice is NOT allowlisted

Attack:
  1. Alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient=alice, ...)  →  pool's msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[pool][router] == true  →  PASSES
  5. Alice's swap executes successfully despite never being allowlisted

Result:
  - Alice bypasses the per-user allowlist entirely.
  - Any address can repeat this, rendering the allowlist guard inoperative.
``` [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L281-295)
```text
    _afterSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      packedSlot0Final,
      bidPriceX64,
      askPriceX64,
      amount0Delta.toInt128(),
      amount1Delta.toInt128(),
      protocolFeeAmount,
      extensionData
    );
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-11)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
```
