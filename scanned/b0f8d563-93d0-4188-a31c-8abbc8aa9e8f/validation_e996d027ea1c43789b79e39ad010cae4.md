### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument it receives from the pool. When `MetricOmmSimpleRouter` is the caller of `pool.swap()`, `sender` is the router's address, not the actual end-user. If the pool admin allowlists the router so that legitimate users can trade through it, every non-allowlisted user can bypass the guard by routing through the same public contract.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap` calls `_beforeSwap` with its own `msg.sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

**Step 2 — The extension checks that forwarded `sender` against the allowlist.**

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

`msg.sender` inside the extension is the pool; `sender` is whoever called `pool.swap()`.

**Step 3 — The router calls `pool.swap()` directly, making itself the `sender`.**

`exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput` all call `IMetricOmmPoolActions(pool).swap(...)` with no mechanism to forward the original `msg.sender`: [4](#0-3) [5](#0-4) 

The pool therefore receives `msg.sender = router`, and the extension checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actual_user]`.

**Step 4 — The forced dilemma.**

A pool admin who deploys a curated pool with `SwapAllowlistExtension` faces an impossible choice:

| Admin decision | Consequence |
|---|---|
| Do **not** allowlist the router | Legitimate users cannot use the router at all |
| **Allowlist the router** | Every non-allowlisted user bypasses the guard by calling any router entry-point |

There is no configuration that simultaneously allows legitimate router users and blocks non-allowlisted ones.

---

### Impact Explanation

Any non-allowlisted address can trade on a pool that was explicitly configured to restrict access. The allowlist — the sole on-chain enforcement mechanism for curated pools — is rendered inoperative for all router-mediated flows. Depending on pool design this enables:

- Unauthorized counterparties executing swaps in a KYC/compliance-gated pool, draining LP value at oracle-derived prices.
- Complete nullification of the admin-configured access boundary, which is an admin-boundary break reachable by an unprivileged path.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary user-facing swap entry point; most end-users interact through it rather than calling the pool directly.
- A pool admin who wants legitimate users to be able to use the router **must** allowlist it, which is the exact configuration that opens the bypass.
- No special setup, flash loan, or privileged role is required — any EOA can call the router.

---

### Recommendation

The extension must be able to identify the economic actor, not the intermediary. Two viable approaches:

1. **Decode the real user from `extensionData`**: Have the router encode `msg.sender` into the `extensionData` it forwards, and have `SwapAllowlistExtension` decode and verify it (with a pool-level flag that requires this encoding, so the extension cannot be trivially spoofed by a direct pool call with crafted bytes).

2. **Check `tx.origin` as a fallback**: When `sender` is a known periphery contract (e.g., the factory-registered router), fall back to `tx.origin`. This is acceptable only if the pool is not intended to be called from other contracts.

The cleanest long-term fix is approach (1): the router encodes its `msg.sender` into `extensionData`, and the extension verifies both that the pool's `msg.sender` is the trusted router and that the decoded user is allowlisted.

---

### Proof of Concept

```
Setup:
  pool = new MetricOmmPool(..., extensions=[SwapAllowlistExtension], ...)
  admin.setAllowedToSwap(pool, alice, true)          // alice is allowed
  admin.setAllowedToSwap(pool, router, true)         // router allowlisted so alice can use it

Attack (executed by eve, who is NOT allowlisted):
  eve calls MetricOmmSimpleRouter.exactInputSingle({
      pool:      pool,
      recipient: eve,
      ...
  })

  → router calls pool.swap(eve_recipient, ...)
  → pool calls _beforeSwap(msg.sender=router, ...)
  → SwapAllowlistExtension.beforeSwap(sender=router, ...)
  → checks allowedSwapper[pool][router] → TRUE  ✓
  → swap executes; eve receives tokens from the curated pool

Result: eve, a non-allowlisted address, successfully trades on a pool
        whose allowlist was supposed to block her.
``` [6](#0-5) [7](#0-6) [4](#0-3)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
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
