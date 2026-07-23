### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which equals `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (the only way to permit router-mediated swaps), every user — including non-allowlisted ones — can bypass the swap gate by calling through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its allowlist check against the `sender` parameter:

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

`msg.sender` here is the pool (correct — only pools can call extensions). `sender` is the first argument forwarded by the pool, which is `msg.sender` of the original `pool.swap()` call:

```solidity
function swap(...) external ... {
    _beforeSwap(
        msg.sender,   // <-- whoever called pool.swap()
        recipient, ...
    );
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [3](#0-2) 

The pool's `msg.sender` is the router, so `sender` in `beforeSwap` is the router address — not the end user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an impossible dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Router swaps fail for **everyone**, including allowlisted users |
| Allowlist the router | **Every** user bypasses the allowlist via the router |

There is no configuration that achieves "only allowlisted users may swap, including via the router." The same problem applies to all router entry points: `exactInput`, `exactOutputSingle`, and `exactOutput` (including the recursive callback path in `_exactOutputIterateCallback`). [4](#0-3) [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-internal actors) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker can execute swaps at oracle-derived prices, draining LP reserves or extracting value from a pool that was intended to be access-controlled. This is a direct loss of the access-control invariant with fund-impacting consequences (unauthorized swap execution against pool reserves).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point documented and deployed alongside the protocol. Any pool admin who deploys a `SwapAllowlistExtension` and also wants to support the standard router workflow will naturally allowlist the router, triggering the bypass. The attacker requires no special privileges, no non-standard tokens, and no malicious setup — only a call to a public router function.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **economically relevant actor** — the end user — not the intermediary. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` for each hop, and the extension decodes and checks it. This requires a trusted router convention and is fragile if other callers omit the field.

2. **Check `recipient` instead of (or in addition to) `sender`**: For swap allowlists, the recipient is often the economically relevant party. However, `recipient` can also be a contract.

3. **Preferred — check both `sender` and `recipient`**: Require that both the caller and the recipient are allowlisted, so routing through an intermediate contract does not help unless the end recipient is also approved.

4. **Alternatively, remove router indirection**: Require that allowlisted pools are only callable directly (not via the router), enforced by checking `tx.origin` or by requiring `sender == recipient`. Note that `tx.origin` checks have their own risks.

The cleanest fix is option 3: in `beforeSwap`, check both `sender` and `recipient` against the allowlist, so a non-allowlisted user cannot benefit from routing through an allowlisted router.

---

### Proof of Concept

```
Setup:
  - Pool P is deployed with SwapAllowlistExtension E configured.
  - Pool admin calls E.setAllowedToSwap(P, router, true)
    (to allow router-mediated swaps for allowlisted users)
  - Alice (address 0xAlice) is NOT in allowedSwapper[P].

Attack:
  1. Alice calls MetricOmmSimpleRouter.exactInputSingle({
       pool: P,
       recipient: Alice,
       zeroForOne: true,
       amountIn: X,
       ...
     })
  2. Router calls P.swap(Alice, true, X, ...)
     → pool.msg.sender = router
  3. Pool calls _beforeSwap(router, Alice, ...)
  4. Extension checks: allowedSwapper[P][router] == true → PASS
  5. Swap executes. Alice receives output tokens.

Expected: revert NotAllowedToSwap (Alice is not allowlisted)
Actual:   swap succeeds (router is allowlisted, Alice bypasses the gate)
``` [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-41)
```text
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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
