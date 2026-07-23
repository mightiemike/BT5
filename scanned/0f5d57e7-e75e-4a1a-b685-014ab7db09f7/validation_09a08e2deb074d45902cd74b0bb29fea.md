### Title
`SwapAllowlistExtension` gates the router address instead of the actual end-user, allowing complete allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router address to enable router-mediated swaps, every unprivileged user can bypass the curated allowlist by calling the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

The result is that the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. There is no mechanism in the router or the pool to thread the original user's address through to the extension.

The pool admin who wants to allow router-mediated swaps must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true`, and the check passes for **every** user who calls the router — regardless of whether that user is on the allowlist. The router is a public, permissionless contract with no access control of its own. [5](#0-4) 

---

### Impact Explanation

A pool deployed with `SwapAllowlistExtension` to restrict swaps to KYC'd or otherwise curated addresses loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker does not need any privileged role; they only need to call a public router function. The pool's LP assets are exposed to swaps from actors the pool admin explicitly intended to exclude, which can result in direct loss of LP value if the pool's curation policy was the primary protection against adverse selection or regulatory risk.

---

### Likelihood Explanation

The trigger is a semi-trusted admin action with a plausible, non-malicious motivation: allowlisting the router so that allowlisted users can access the pool through the standard periphery. The admin has no on-chain signal that doing so opens the gate to all users. The router is a deployed, public contract. Any user who discovers the allowlisted router address can exploit this immediately with a single `exactInputSingle` call.

---

### Recommendation

The `sender` argument forwarded to extensions should represent the economic actor, not the intermediary contract. Two complementary fixes:

1. **In `MetricOmmSimpleRouter`**: pass the original `msg.sender` (the end user) as the `sender` argument to `pool.swap()` rather than relying on the pool to use `msg.sender`. The pool's `swap` signature already accepts a `recipient` that differs from the caller; a parallel `sender` override parameter would allow the router to identify the true initiator.

2. **In `SwapAllowlistExtension`**: document clearly that `sender` is the direct caller of `pool.swap()`, and that allowlisting a router grants access to all users of that router. Until the router threading is fixed, the extension should not be used with router-mediated pools unless `allowAllSwappers` is the intended policy.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension (beforeSwap order = extension 1)
  allowedSwapper[pool][alice]   = true   (alice is the intended curated user)
  allowedSwapper[pool][router]  = true   (admin allowlists router for periphery access)
  allowedSwapper[pool][attacker]= false  (attacker is explicitly excluded)

Attack:
  attacker calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient=attacker, ...)
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → checks allowedSwapper[pool][router] == true  ✓
    → swap executes for attacker

Result:
  attacker swaps successfully despite being excluded from the allowlist.
  The curated pool's protection is completely bypassed.
``` [3](#0-2) [6](#0-5) [7](#0-6)

### Citations

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
