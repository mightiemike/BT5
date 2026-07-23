### Title
SwapAllowlistExtension gates the router address instead of the actual swapper, enabling complete allowlist bypass when the router is allowlisted — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` is the caller, `sender` is the router's address, not the end user's. A pool admin who allowlists the router so that allowlisted users can reach the pool through the standard periphery path inadvertently opens the allowlist to every user of that public router.

---

### Finding Description

**Pool → Extension argument binding**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

**What the extension checks**

`SwapAllowlistExtension.beforeSwap` uses `msg.sender` (the pool) as the mapping key and `sender` (the immediate caller of the pool) as the identity being gated: [3](#0-2) 

**What the router sends**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly. The pool's `msg.sender` is therefore the router contract, not the end user: [4](#0-3) 

The router stores the real payer in transient storage for the callback but never forwards that address to the pool or to any extension: [5](#0-4) 

**The irreconcilable tension**

| Admin choice | Effect on allowlisted users | Effect on non-allowlisted users |
|---|---|---|
| Router **not** allowlisted | Cannot use the router; must call pool directly | Correctly blocked |
| Router **allowlisted** | Can use the router | **Also pass** — full bypass |

There is no configuration that simultaneously lets allowlisted users use the router and blocks non-allowlisted users from doing the same, because the extension cannot distinguish the two once the router is the immediate caller.

---

### Impact Explanation

Any user who routes through `MetricOmmSimpleRouter` can swap on a pool whose `SwapAllowlistExtension` has the router address in `allowedSwapper`. The pool transfers output tokens to the caller's chosen `recipient` and collects input tokens via the swap callback. A non-allowlisted user therefore receives real token output from a pool that was configured to deny them access. The financial loss is the full value of every swap executed by non-allowlisted users on the restricted pool.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the protocol's standard swap interface. A pool admin who wants allowlisted users to be able to use the router — the expected and documented periphery path — must allowlist the router address. This is a natural, non-malicious administrative action. The admin has no on-chain signal that doing so opens the allowlist to everyone. The bypass is therefore reachable whenever a curated pool is deployed with the intent of supporting router-based swaps.

---

### Recommendation

The extension must gate the actual end user, not the intermediate contract. Two complementary approaches:

1. **Router-forwarded identity**: Have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` for each hop, and update `SwapAllowlistExtension.beforeSwap` to decode and check that address when `sender` is a known router.

2. **Per-user allowlist on the router**: Alternatively, the extension can check `sender` only when `sender` is not a recognized router, and require the router to attest the real user via a signed or transient-storage mechanism.

The simplest safe fix is option 1: the router appends `abi.encode(msg.sender)` to `extensionData`, and the extension reads it when `sender` matches a known router address, falling back to `sender` for direct pool calls.

---

### Proof of Concept

```
Setup
─────
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Admin calls setAllowedToSwap(pool, userA, true)   // allowlist a KYC'd user
3. Admin calls setAllowedToSwap(pool, router, true)  // allow allowlisted users to use the router

Attack
──────
4. userB (not allowlisted) calls:
       router.exactInputSingle({
           pool:      <restricted pool>,
           recipient: userB,
           zeroForOne: true,
           amountIn:  X,
           ...
       })

5. Router calls pool.swap(userB, true, X, ...) — pool's msg.sender = router.

6. Pool calls _beforeSwap(sender=router, ...).

7. SwapAllowlistExtension checks:
       allowedSwapper[pool][router]  →  true   ✓ (step 3)

8. Swap executes. userB receives output tokens from the restricted pool.

Result: userB, who is not on the allowlist, successfully swaps on a pool
        that was configured to deny them access.
``` [3](#0-2) [6](#0-5) [7](#0-6)

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
