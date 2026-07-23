### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the end user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][end_user]`. If the pool admin allowlists the router (the natural setup for a curated pool that also wants to support router-mediated swaps), every unpermissioned user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← router address when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks the allowlist keyed on that `sender`:

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

`msg.sender` inside the extension is the pool (correct), and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant) calls `pool.swap(...)`, the pool sees `msg.sender = router`, so the extension evaluates `allowedSwapper[pool][router]`.

A pool admin who wants to restrict swaps to a curated set of users will:
1. Deploy the pool with `SwapAllowlistExtension` in the `beforeSwap` order.
2. Allowlist individual user addresses via `setAllowedToSwap`.
3. Also allowlist the router so that allowlisted users can reach the pool through the standard periphery path.

Step 3 is the trap: once `allowedSwapper[pool][router] = true`, **any** address can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) and the extension will pass because it sees the router as the sender, not the end user.

---

### Impact Explanation

Any unpermissioned user can trade on a curated pool that is supposed to be restricted to an allowlist. Consequences include:

- **Unauthorized extraction of LP value**: curated pools are often set up with specific counterparty assumptions (e.g., only KYC'd addresses, only protocol-owned addresses). Bypassing the allowlist lets arbitrary actors drain the spread/fee revenue or execute adversarial trades against the pool's LPs.
- **Broken core pool functionality**: the allowlist is the primary access-control mechanism for curated pools; its bypass renders the entire curation model ineffective.
- **Direct loss of user principal**: LPs who deposited under the assumption that only authorized counterparties would trade against them are exposed to unauthorized flow.

---

### Likelihood Explanation

The bypass is trivially reachable by any user who knows the pool uses a `SwapAllowlistExtension`. The router is a public, permissionless contract. No special privileges, flash loans, or multi-step setup are required — a single `exactInputSingle` call through the router suffices. The scenario where the router is allowlisted is the natural production configuration (otherwise allowlisted users cannot use the standard periphery), making this a near-certain misconfiguration for any curated pool that also wants router support.

---

### Recommendation

The extension must gate on the **end user**, not the immediate pool caller. Two complementary fixes:

1. **Pass the original user through the router**: `MetricOmmSimpleRouter` already stores the original `msg.sender` in transient storage as the payer. The pool could expose a hook-level "originator" field, or the router could pass the real user in `extensionData` for the extension to decode and verify.

2. **Alternatively, check `sender` only when `sender` is not a known periphery contract**: the extension could maintain a registry of trusted routers and, when `sender` is a router, fall back to verifying the address stored in the router's transient callback context.

The simplest safe fix is to have the router encode the real user's address in `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that address when the immediate `sender` is a recognized router.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension in beforeSwap slot
  allowedSwapper[pool][alice] = true          // alice is the only allowed user
  allowedSwapper[pool][router] = true         // admin adds router so alice can use it

Attack (executed by bob, who is NOT allowlisted):
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool, recipient: bob, ...})
  2. router calls pool.swap(bob, zeroForOne, amount, limit, "", extensionData)
     → pool.msg.sender = router
  3. pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension.beforeSwap receives sender=router
     → checks allowedSwapper[pool][router] == true  ✓
     → does NOT revert
  5. swap executes; bob receives output tokens

Result: bob, who is not on the allowlist, successfully swaps on a curated pool.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
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
