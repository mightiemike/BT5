### Title
`SwapAllowlistExtension` Checks Immediate Caller Instead of End User, Allowing Router-Mediated Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate caller of `MetricOmmPool.swap()`. When `MetricOmmSimpleRouter` intermediates the call, `sender` resolves to the router's address, not the end user's. A pool admin who allowlists the router (the natural production step to make the router usable on a curated pool) inadvertently grants every user of that router the ability to bypass the per-user swap allowlist.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

When `MetricOmmSimpleRouter` calls `pool.swap(...)`, the pool's `msg.sender` is the router contract. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`. Any pool admin who adds the router to the allowlist (so that router-mediated swaps work at all) simultaneously grants every user of that router the ability to trade on the curated pool, regardless of whether those individual users are on the allowlist.

The `DepositAllowlistExtension` does not share this exact flaw because it checks the `owner` parameter (which the pool passes as the caller-supplied `owner` argument to `addLiquidity`), not `msg.sender` of the pool call. The swap path has no equivalent owner-level identity forwarding.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd addresses, institutional partners, or whitelisted market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker does not need any special privilege: they simply call the router. The pool receives trades from arbitrary addresses, violating the curation invariant the extension was deployed to enforce.

Impact: **High** — complete bypass of the swap allowlist on any production pool that uses the router alongside `SwapAllowlistExtension`.

---

### Likelihood Explanation

The scenario is the natural production configuration. A pool admin who deploys `SwapAllowlistExtension` and also wants users to access the pool through the supported periphery router must allowlist the router. There is no documented warning or alternative path. Any pool that reaches this configuration is fully exposed. Likelihood: **High**.

---

### Recommendation

The extension must gate on the end user's identity, not the immediate caller of `swap()`. Two complementary fixes:

1. **Router-level forwarding**: Have `MetricOmmSimpleRouter` pass the originating user's address in `extensionData`, and update `SwapAllowlistExtension.beforeSwap` to decode and check that address when `sender` is a known router.

2. **Extension-level design**: Alternatively, redesign `SwapAllowlistExtension` to maintain a separate allowlist for trusted intermediaries (routers) and require that `extensionData` carries a signed or verified end-user identity when the immediate caller is an intermediary.

The simplest safe fix is to have the router pass `msg.sender` (the end user) in `extensionData` and have the extension decode it:

```solidity
// In SwapAllowlistExtension.beforeSwap:
address effectiveSender = extensionData.length >= 20
    ? abi.decode(extensionData, (address))
    : sender;
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][effectiveSender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

This requires the router to always populate `extensionData` with the originating user and the extension to enforce that trusted routers must supply it.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as `extension1` with `beforeSwap` order set.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Non-allowlisted user `alice` calls `MetricOmmSimpleRouter.exactInput(...)` targeting the pool.
4. The router calls `pool.swap(recipient, ...)` — `msg.sender` inside the pool is the router.
5. `_beforeSwap(router, recipient, ...)` is dispatched; the extension evaluates `allowedSwapper[pool][router]` → `true`.
6. The swap executes. `alice` has traded on a pool she was never individually allowlisted for.

Direct call by `alice` to `pool.swap(...)` would correctly revert with `NotAllowedToSwap` because `allowedSwapper[pool][alice]` is `false`. The router path silently bypasses this check. [4](#0-3) [5](#0-4) [2](#0-1)

### Citations

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
