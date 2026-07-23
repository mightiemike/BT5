The call chain is fully traceable. Here is the analysis:

---

## Call Chain Trace

**Direct swap** (`user → pool`):

```
user calls MetricOmmPool.swap(...)
  → pool calls _beforeSwap(msg.sender=user, ...)          [ExtensionCalling.sol:230]
    → extension.beforeSwap(sender=user, ...)
      → checks allowedSwapper[msg.sender][sender]
                           = allowedSwapper[pool][user]   ✓ correct identity
```

**Router swap** (`user → router → pool`):

```
user calls MetricOmmSimpleRouter.exactInputSingle(...)
  → router calls pool.swap(...)                           [MetricOmmSimpleRouter.sol:72-80]
    → pool calls _beforeSwap(msg.sender=router, ...)      [MetricOmmPool.sol:230-240]
      → extension.beforeSwap(sender=router, ...)
        → checks allowedSwapper[msg.sender][sender]
                             = allowedSwapper[pool][router]  ✗ wrong identity
```

The `sender` argument passed to `beforeSwap` is always `msg.sender` from within `MetricOmmPool.swap`, which is the **router's address** when the router intermediates the call — not the original user. [1](#0-0) [2](#0-1) 

---

## Verdict

### Title
Router-Mediated Swaps Corrupt Swapper Identity in SwapAllowlistExtension, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps using `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, the `sender` seen by the hook is the **router's address**, not the original user's address. This breaks the allowlist in two symmetric ways.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [3](#0-2) 

`ExtensionCalling._beforeSwap` forwards that `sender` verbatim to the extension: [4](#0-3) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [5](#0-4) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant) calls `pool.swap`, the pool's `msg.sender` is the router: [6](#0-5) 

So the hook evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`.

**Two failure modes arise:**

| Admin configuration | Result |
|---|---|
| Allowlists specific users, does NOT allowlist router | Allowlisted users **cannot** use the router (broken functionality) |
| Allowlists the router (to enable router usage) | **Any** user can bypass the per-user allowlist via the router |

The second mode is the exploitable path: a pool admin who wants to support router-mediated swaps for their allowlisted users must add the router to the allowlist. Once the router is allowlisted, any unprivileged user can call `exactInputSingle`/`exactOutputSingle`/`exactInput`/`exactOutput` and the hook passes, because `allowedSwapper[pool][router] == true`.

### Impact Explanation

The `SwapAllowlistExtension` is the primary access-control mechanism for restricting who may trade in a pool. If the router is allowlisted (the only way to support router usage), the allowlist is rendered completely ineffective — any address can swap. This breaks core pool functionality and, depending on the pool's purpose (e.g., private institutional pool, KYC-gated pool), allows unauthorized parties to execute trades, potentially draining one side of the pool at oracle-derived prices.

### Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` and also wants to support the canonical `MetricOmmSimpleRouter` must allowlist the router. This is a natural and expected configuration. The bypass requires no special privileges, no flash loans, and no multi-transaction sequencing — a single `exactInputSingle` call suffices.

The "timed-threshold / two-transaction" framing in the question is a red herring. The identity confusion is structural and exploitable in a single transaction.

### Recommendation

Pass the **original initiator** through the call chain rather than the direct `msg.sender`. One approach: have the router encode the original `msg.sender` in `extensionData`, and have the extension decode and verify it. A cleaner approach: add an `originator` field to the swap call that the pool passes to hooks, with the pool enforcing that direct callers set `originator == msg.sender` while trusted routers may set it to the actual user.

Alternatively, the allowlist admin documentation must explicitly warn that allowlisting the router grants access to all users, and the extension should provide a separate `allowedRouter` mapping that the hook uses to unwrap the true sender from `extensionData`.

### Proof of Concept

```
1. Pool P is deployed with SwapAllowlistExtension E configured.
2. Admin calls E.setAllowedToSwap(P, alice, true)       // alice is the only allowed swapper
3. Admin calls E.setAllowedToSwap(P, router, true)      // router added to support alice's UX
4. Attacker (bob, not allowlisted) calls:
       router.exactInputSingle({pool: P, ...})
5. Pool calls _beforeSwap(msg.sender=router, ...)
6. Hook checks allowedSwapper[P][router] == true  →  PASSES
7. Bob's swap executes despite not being on the allowlist.
``` [7](#0-6) [8](#0-7)

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
