### Title
SwapAllowlistExtension Gates the Router Address Instead of the Economic Actor, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, that value is the router's address, not the user's EOA. A pool admin who allowlists the router to let their approved users trade through the standard periphery simultaneously opens the gate to every user on the network, completely defeating the allowlist.

---

### Finding Description

**Root cause — wrong actor in the allowlist lookup**

`SwapAllowlistExtension.beforeSwap` performs:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool — which is `msg.sender` of `pool.swap()`.

**How the pool populates `sender`**

`MetricOmmPool.swap` calls:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` encodes that value verbatim as the `sender` argument to the extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [3](#0-2) 

**How the router breaks the identity**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making `msg.sender` inside the pool equal to the router contract, not the user's EOA:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [4](#0-3) 

The same pattern applies to `exactInput` and `exactOutputSingle`. [5](#0-4) 

**The dilemma the admin faces**

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the standard periphery; router-mediated swaps revert for everyone |
| **Allowlist the router** | `allowedSwapper[pool][router] = true` → the check `allowedSwapper[msg.sender][sender]` passes for **any** user who calls the router, because `sender` = router for all of them |

The second branch is the exploit path. The router is a public, permissionless contract with no access control of its own. [6](#0-5) 

**Analogy to the seeded bug class**

The NFT bug checked `_isApprovedForAll[msgSender_][spender_]` (the caller's own approval map) instead of `_isApprovedForAll[_owner][msgSender_]` (the token owner's approval map) — the wrong address occupied the first mapping key. Here, `allowedSwapper[pool][sender]` uses `sender = router` instead of `sender = user EOA` — the wrong address occupies the second mapping key, with the same structural consequence: a self-referential or intermediary address satisfies the check on behalf of any real actor.

---

### Impact Explanation

**Severity: High**

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, whitelisted market makers). The moment the admin allowlists the router — a necessary step for allowlisted users to access the standard swap UI — the allowlist is nullified. Any unprivileged EOA can call `exactInputSingle` or `exactInput` on the router and trade on the supposedly restricted pool. This is a direct, complete bypass of a core access-control mechanism with no additional preconditions beyond the admin's own reasonable configuration step.

---

### Likelihood Explanation

**Likelihood: High**

The `MetricOmmSimpleRouter` is the canonical periphery swap entry point. Pool admins who want their allowlisted users to trade normally will allowlist the router. The bypass requires no special knowledge, no privileged keys, and no unusual token behavior — any user who can call the router (i.e., everyone) can exploit it the moment the router is allowlisted.

---

### Recommendation

The extension must gate the **economic actor** — the original user — not the direct caller of `pool.swap()`. Two complementary fixes:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode the originating user's address into `extensionData` so the extension can decode and check it. This requires a convention between the router and the extension.

2. **Extension-side (simpler)**: Change `SwapAllowlistExtension.beforeSwap` to check `recipient` when `sender` is a known router, or — better — require that the pool admin allowlists individual user EOAs and that the router is never itself allowlisted. Document this constraint explicitly.

The cleanest long-term fix is for the router to forward the original `msg.sender` in a standardized field of `extensionData`, and for the extension to decode and verify that field:

```diff
- if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
+ address actor = _decodeActorFromExtensionData(extensionData, sender);
+ if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][actor]) {
```

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// Admin also allowlists the router so allowedUser can trade via UI.
extension.setAllowedToSwap(pool, allowedUser, true);
extension.setAllowedToSwap(pool, address(router), true); // ← admin's "fix" for router support

// Attacker (not allowlisted):
vm.startPrank(attacker);
token0.approve(address(router), type(uint256).max);

// Router call: pool.swap() sees msg.sender = router → allowedSwapper[pool][router] = true → passes
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        recipient:       attacker,
        zeroForOne:      true,
        amountIn:        1e18,
        amountOutMinimum: 0,
        priceLimitX64:   0,
        deadline:        block.timestamp,
        extensionData:   ""
    })
);
vm.stopPrank();
// attacker successfully swapped on a pool that should have blocked them
``` [7](#0-6) [8](#0-7)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L19-24)
```text
contract MetricOmmSimpleRouter is MetricOmmSwapRouterBase, PeripheryPayments, SelfPermit, IMetricOmmSimpleRouter {
  /// @notice Transient callback mode is not supported by this router.
  /// @param callbackMode Unrecognized mode read from transient storage.
  error InvalidCallbackMode(uint8 callbackMode);

  constructor(address weth, address factory) MetricOmmSwapRouterBase(factory) PeripheryPayments(weth) {}
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
