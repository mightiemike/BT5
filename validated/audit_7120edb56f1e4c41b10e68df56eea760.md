### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the extension checks the router's address against the allowlist — not the actual end-user's address. A pool admin who allowlists the router to enable router-mediated swaps for permitted users inadvertently opens the gate to every user on the router, completely defeating the allowlist.

---

### Finding Description

**Step 1 — Pool forwards `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap` calls `_beforeSwap` with its own `msg.sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value as the `sender` argument and dispatches it to every configured extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)
)
``` [2](#0-1) 

**Step 2 — SwapAllowlistExtension checks that forwarded `sender`.**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [3](#0-2) 

`msg.sender` inside the extension is the pool (correct key for the per-pool mapping). `sender` is whatever the pool received as its own `msg.sender`.

**Step 3 — MetricOmmSimpleRouter is the pool's `msg.sender`.**

`exactInputSingle` (and every other router entry point) calls `pool.swap(...)` directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

The pool therefore sees `msg.sender = router`. It forwards `router` as `sender` to the extension. The extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actualUser]`.

**Step 4 — The invariant is permanently broken.**

The pool admin faces an impossible choice:

| Admin configuration | Result |
|---|---|
| Allowlist individual users, not the router | Allowlisted users **cannot** use the router (router address fails the check) |
| Allowlist the router | **Every** user can bypass the allowlist via the router |

There is no configuration that simultaneously allows allowlisted users to swap through the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

Any user who is not on the allowlist can execute swaps on a restricted pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`). The pool receives real token input and delivers real token output at the oracle price. The allowlist guard — the only mechanism preventing unauthorized access — is silently bypassed. Pools configured for regulatory compliance, KYC gating, or partner-only access are fully open to any address that routes through the public router.

---

### Likelihood Explanation

The bypass is reachable whenever the pool admin allowlists the router to support router-mediated swaps for permitted users — a natural and expected configuration. The router is a public, permissionless contract. Once the router is allowlisted, no further attacker capability is required: any EOA can call `exactInputSingle` with a non-zero amount and a valid price limit.

---

### Recommendation

The extension must check the identity of the economic actor, not the intermediary. Two sound approaches:

1. **Pass the real user in `extensionData`**: Have the router encode `msg.sender` (the actual user) into `extensionData` before forwarding to the pool. The extension decodes and checks that address when `sender` is a known router. This requires the extension to maintain a registry of trusted routers.

2. **Check `sender` only for direct callers; require routers to self-identify**: Define a router interface that exposes the originating user, and have the extension call back into the router when `sender` is not a plain EOA.

The simplest safe default is to treat any non-EOA `sender` as untrusted and revert unless `allowAllSwappers[pool]` is set, forcing admins to explicitly opt in to router-mediated access.

---

### Proof of Concept

```
Setup:
  pool P configured with SwapAllowlistExtension E
  allowedSwapper[P][alice] = true          // alice is the only permitted swapper
  allowedSwapper[P][router] = true         // admin adds router so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({
        pool: P,
        zeroForOne: true,
        amountIn: X,
        recipient: bob,
        ...
    })

Execution trace:
  router → pool.swap(bob, true, X, ...)
    pool: msg.sender = router
    pool calls _beforeSwap(sender=router, ...)
    extension checks allowedSwapper[P][router] → true  ✓ (passes)
    swap executes; bob receives tokens

Result:
  bob, who is not on the allowlist, successfully swaps.
  The allowlist guard is completely bypassed.
``` [3](#0-2) [1](#0-0) [5](#0-4)

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
