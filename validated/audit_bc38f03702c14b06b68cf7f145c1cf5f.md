### Title
`SwapAllowlistExtension` gates on the router's address instead of the actual swapper, allowing any user to bypass the allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` from the pool's perspective — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool sees `sender = router`, not the actual user. If the pool admin allowlists the router (a necessary step to let any allowlisted user trade via the router), every unprivileged user can bypass the allowlist by routing through the same public contract.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension caller) and `sender` is the first argument forwarded by the pool — which is `msg.sender` at the time `pool.swap()` was called.

In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender` to the extension dispatcher:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` on the user's behalf:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

The pool therefore calls `extension.beforeSwap(router, ...)`. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The actual user's identity is never verified.

This creates an irreconcilable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user — including non-allowlisted ones — can bypass the gate via the router |

---

### Impact Explanation

Any user who is not on the allowlist can execute swaps on a restricted pool by routing through `MetricOmmSimpleRouter`. The allowlist — the sole access-control boundary for swap permissions — is rendered ineffective the moment the router is allowlisted. Pools intended for specific counterparties (KYC-gated, market-maker-only, or compliance-restricted) are fully open to arbitrary swappers via the public router.

---

### Likelihood Explanation

The bypass requires the pool admin to have allowlisted the router address. This is a natural and expected operational step: any pool that wants to support router-mediated swaps for its allowlisted users must allowlist the router. The bypass is therefore reachable in any production deployment that uses both `SwapAllowlistExtension` and `MetricOmmSimpleRouter`.

---

### Recommendation

The extension must verify the identity of the **economic actor**, not the intermediary. Two viable approaches:

1. **Pass the real user through `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it. This requires a trust assumption that the router is the only allowlisted intermediary and that it faithfully encodes the real caller.

2. **Allowlist at the router level, not the extension level**: The router should enforce the allowlist before calling `pool.swap()`, and the extension should only allowlist the router itself. The router's `exactInputSingle` / `exactInput` / `exactOutput` entry points would check the caller against a separate allowlist before forwarding.

3. **Remove router allowlisting entirely**: Only allowlist EOAs. Allowlisted users must call `pool.swap()` directly. Document this constraint explicitly.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin: setAllowedToSwap(pool, alice, true)      // alice is a legitimate counterparty
  admin: setAllowedToSwap(pool, router, true)     // needed so alice can use the router

Attack:
  charlie (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: charlie, ...})

Execution trace:
  router.exactInputSingle()
    → pool.swap(charlie, zeroForOne, amount, ...)   // msg.sender = router
      → _beforeSwap(router, charlie, ...)
        → extension.beforeSwap(router, charlie, ...)
          → allowedSwapper[pool][router] == true    // ✓ passes
      → swap executes at oracle price
      → charlie receives output tokens

Result:
  charlie swaps successfully on a pool he is not allowlisted for.
  The allowlist is bypassed.
``` [4](#0-3) [5](#0-4) [2](#0-1) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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
