### Title
`SwapAllowlistExtension` checks the router's address instead of the actual user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. This creates an irresolvable dilemma: either the router is allowlisted (any user bypasses the gate) or it is not (allowlisted users cannot use the router at all).

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`:

```solidity
// metric-core/contracts/MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← pool's caller, not the end-user
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value unchanged as the first argument to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)   // sender = pool's msg.sender
)
``` [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks that `sender` against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne, ...
);
``` [4](#0-3) 

At that point `pool.msg.sender` is the **router contract**, not the end-user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` parameter (the economic actor), not `sender`. The swap extension has no equivalent forwarding of the real user identity. [5](#0-4) 

---

### Impact Explanation

Two mutually exclusive failure modes, both fund-impacting:

**Mode A — Allowlist bypass (High):** The pool admin allowlists the router address so that legitimate users can reach the pool through the standard periphery path. Because the extension only sees the router, every user — including those explicitly excluded from the allowlist — can now trade on the curated pool by routing through `MetricOmmSimpleRouter`. The entire curation policy (KYC, institutional-only, compliance gating) is silently voided.

**Mode B — Broken core functionality (Medium):** The pool admin does not allowlist the router. Allowlisted users who call through the router are rejected because the extension sees the router address, which is not on the list. The only usable path is a direct `pool.swap()` call, which requires the user to implement their own callback settlement — the standard periphery is unusable for any allowlisted pool.

Both modes are reachable by any unprivileged user through the public `MetricOmmSimpleRouter` entry points.

---

### Likelihood Explanation

- `SwapAllowlistExtension` is a production extension in `metric-periphery` explicitly designed for curated pools.
- `MetricOmmSimpleRouter` is the primary user-facing swap path; most integrations and front-ends route through it.
- No admin action is required to trigger the bypass; any user can call the router.
- The pool admin has no on-chain mechanism to distinguish "router called on behalf of user X" from "router called on behalf of user Y" — the extension only receives the router address.

---

### Recommendation

The extension must receive and check the **economically relevant actor**, not the intermediary caller. Two approaches:

1. **Pass the original user through the router:** Have `MetricOmmSimpleRouter` forward `msg.sender` as an additional field in `extensionData`, and update `SwapAllowlistExtension` to decode and check that address when `sender` is a known router. This requires a trust assumption on the router.

2. **Check `sender` at the pool level before extension dispatch:** The pool could expose the original initiator (e.g., via a dedicated field in the swap parameters), so extensions always receive the true end-user regardless of intermediary.

The cleaner fix is option 2: add an explicit `originator` parameter to `IMetricOmmPoolActions.swap()` that the pool passes through to extensions, with the router setting it to `msg.sender` before calling the pool.

---

### Proof of Concept

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` configured. Only `alice` is allowlisted: `allowedSwapper[pool][alice] = true`.
2. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
3. The router calls `pool.swap(recipient, ...)`. Inside the pool, `msg.sender = router`.
4. The pool calls `extension.beforeSwap(sender=router, ...)`.
5. The extension evaluates `allowedSwapper[pool][router]`.
6. If the pool admin previously allowlisted the router (to let `alice` use it), this check passes and `bob`'s swap executes — allowlist bypassed.
7. `bob` receives tokens from the curated pool despite being explicitly excluded. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
