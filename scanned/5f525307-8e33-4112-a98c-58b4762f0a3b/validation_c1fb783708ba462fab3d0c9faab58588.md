### Title
SwapAllowlistExtension Checks Router Address Instead of Original Swapper, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap` call. When a user routes through the public `MetricOmmSimpleRouter`, `sender` is the router's address, not the original EOA. If the pool admin allowlists the router (a necessary step to let allowlisted users perform multi-hop swaps), every user — including those explicitly excluded from the allowlist — can bypass the gate by routing through the router.

---

### Finding Description

**Root cause — wrong actor checked:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every before-swap hook:

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap, not the original EOA
    recipient,
    ...
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` value against the per-pool allowlist:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [2](#0-1) 

**Router path — sender is always the router:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly with no forwarding of the original caller:

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
``` [3](#0-2) 

Because the router is the direct caller of `pool.swap`, `msg.sender` inside the pool is the router address. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][original_user]`.

The same applies to `exactInput` (multi-hop) and `exactOutput` / `exactOutputSingle`. [4](#0-3) 

**The trap — allowlisting the router defeats the allowlist:**

The pool admin has two mutually exclusive choices:
- **Do not allowlist the router** → allowlisted users cannot use the router for multi-hop swaps (broken UX).
- **Allowlist the router** → every user, including those explicitly excluded, can bypass the allowlist by routing through the public router.

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified addresses, institutional partners, or protocol-controlled addresses) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Unauthorized users can execute swaps against the pool's LP positions at oracle prices, exposing LPs to unwanted counterparty risk and potentially draining pool liquidity through adversarial trading that the allowlist was designed to prevent.

---

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router — a natural operational step for any curated pool that wants to support multi-hop routing for its permitted users. The router is a public, permissionless contract; once the router address is in the allowlist, any EOA can exploit the bypass without any further privileged action. The bypass is reachable by any unprivileged user with no special setup beyond calling the public router.

---

### Recommendation

The extension must check the original EOA, not the direct caller of `pool.swap`. Two approaches:

1. **Pass the original sender through `extensionData`**: The router encodes `msg.sender` (the original user) into `extensionData` before calling the pool. The extension decodes and checks that address. This requires a coordinated change to the router and the extension.

2. **Require direct pool interaction for allowlisted pools**: Document that pools using `SwapAllowlistExtension` must not allowlist the router, and that allowlisted users must call `pool.swap` directly. This is a usage restriction, not a code fix, and is fragile.

The cleanest fix is approach 1: the router should include the original `msg.sender` in a standardized field of `extensionData`, and the extension should decode and verify that field instead of the `sender` argument.

---

### Proof of Concept

```
Setup:
  - Pool P is deployed with SwapAllowlistExtension (extension E).
  - Admin allowlists Alice:  E.setAllowedToSwap(P, alice, true)
  - Admin allowlists router: E.setAllowedToSwap(P, router, true)
    (necessary so Alice can use multi-hop routing)

Attack (Bob, not allowlisted):
  1. Bob calls router.exactInputSingle({pool: P, ...})
  2. Router calls P.swap(recipient, ...) — msg.sender = router
  3. Pool calls E.beforeSwap(router, ...) — msg.sender = pool
  4. E checks: allowedSwapper[P][router] == true  ✓
  5. Swap executes; Bob receives output tokens from the curated pool.

Result: Bob bypasses the allowlist entirely. The extension never sees Bob's address.
``` [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
