### Title
`SwapAllowlistExtension` Allowlist Bypassed via `MetricOmmSimpleRouter` Due to Wrong Actor Binding — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` — and therefore the `sender` forwarded to the extension — is the **router contract**, not the actual user. A pool admin who allowlists the router to enable router-mediated swaps simultaneously opens the pool to every user, defeating the allowlist entirely.

---

### Finding Description

**`SwapAllowlistExtension.beforeSwap` checks the wrong actor.**

The extension receives `sender` from the pool and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the pool calls the extension), and `sender` is whatever `MetricOmmPool.swap()` passes as its first argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // <-- caller of pool.swap(), NOT the end user when router is used
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` faithfully forwards this value:

```solidity
// metric-core/contracts/ExtensionCalling.sol
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [3](#0-2) 

**`MetricOmmSimpleRouter` is the direct caller of `pool.swap()`.**

For `exactInputSingle`, the router calls the pool directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

The router stores the real user in transient storage for the payment callback, but **never passes it to the pool's `swap()` call**. The pool therefore sees `msg.sender = router`, and the extension checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][realUser]`.

The same pattern holds for `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

**The dilemma the pool admin faces:**

| Router allowlisted? | Allowlisted users via router | Non-allowlisted users via router |
|---|---|---|
| No | Blocked (unusable router) | Blocked |
| Yes | Allowed | **Also allowed — bypass** |

There is no configuration that allows allowlisted users to use the router while blocking non-allowlisted users, because the extension cannot distinguish between them once the router is the `sender`.

---

### Impact Explanation

Any pool that deploys `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified users, whitelisted market makers, or protocol-controlled addresses) has its restriction completely nullified for any user who routes through `MetricOmmSimpleRouter`. The attacker does not need any special privilege — they simply call the public router instead of the pool directly. This is a direct loss of the curation guarantee the pool admin paid to enforce, and allows unauthorized principals to drain pool liquidity at oracle-derived prices.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool admin who wants router-mediated swaps to work for their allowlisted users must allowlist the router. The moment they do, the bypass is live for every address on the network. The attacker needs zero capital beyond the swap input and zero privileged access.

---

### Recommendation

The extension must gate the **economically responsible actor** — the end user — not the intermediary router. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool. The `SwapAllowlistExtension` decodes and verifies it. This requires the extension to trust that the router correctly populates the field (or that only trusted routers are allowlisted).

2. **Check `sender` only for direct pool calls; require a trusted-router registry**: The extension maintains a set of trusted routers. When `sender` is a trusted router, it reads the real user from a standardized field in `extensionData`. When `sender` is not a trusted router, it checks `sender` directly.

Either way, the extension must stop treating the router address as the identity to gate.

---

### Proof of Concept

```
Setup:
  - Pool P deployed with SwapAllowlistExtension E
  - Pool admin calls E.setAllowedToSwap(P, Alice, true)   // Alice is KYC'd
  - Pool admin calls E.setAllowedToSwap(P, router, true)  // needed for router-mediated swaps

Attack:
  - Bob (not allowlisted) calls:
      MetricOmmSimpleRouter.exactInputSingle({
          pool: P,
          recipient: Bob,
          ...
      })
  - Router calls P.swap(Bob, ...) with msg.sender = router
  - Pool calls E.beforeSwap(router, Bob, ...)
  - Extension checks: allowedSwapper[P][router] == true  ✓
  - Bob's swap executes successfully despite not being on the allowlist

Result:
  - Bob swaps in a pool that should have blocked him
  - The allowlist provides zero protection for router-mediated swaps
``` [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-42)
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
}
```

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
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
