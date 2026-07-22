### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` enforces its per-user gate by checking the `sender` argument, which the pool always sets to `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` is the caller, `sender` is the router's address, not the end-user's address. Any pool admin who allowlists the router to enable router-based swaps simultaneously opens the gate to every user on-chain, making the per-user allowlist a no-op for the router path.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every extension hook: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of the pool: [3](#0-2) 

When `MetricOmmSimpleRouter` calls `pool.swap(...)`, the pool's `msg.sender` is the router. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. For any router-based swap to succeed, the pool admin must allowlist the router address. The moment the router is allowlisted, every address on-chain can bypass the per-user restriction by routing through it — the extension's configured guard is rendered superfluous for the entire router path.

The `DepositAllowlistExtension` is not affected in the same way because it checks the `owner` parameter (the position owner supplied by the caller), not `sender`: [4](#0-3) 

---

### Impact Explanation

A curated pool that deploys `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The allowlist is a configured guard that is declared and stored correctly but is never applied to the actual economic actor (the end-user) on the router path. Any non-allowlisted address can execute swaps against the pool's LP reserves, breaking the pool's intended access-control invariant and exposing LP funds to trading by parties the pool admin explicitly excluded.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary supported periphery entry point for swaps. A pool admin who wants to allow even a single allowlisted user to use the router must allowlist the router itself, which simultaneously opens the gate to all users. The bypass requires no special privileges, no malicious setup, and no non-standard tokens — only a call to the public `exactInputSingle` or `exactInput` function on the router.

---

### Recommendation

The extension must gate on the identity of the economic actor, not the immediate pool caller. Two sound approaches:

1. **Check `recipient` instead of `sender`** — the recipient is the address that receives the output tokens and is the natural economic beneficiary of the swap. The router always sets `recipient` to the user-supplied address.
2. **Pass the originating user explicitly** — add an `originSender` field to `extensionData` that the router populates with `msg.sender`, and have the extension decode and verify it. The pool should validate that the router is a trusted forwarder before trusting this field.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists only `alice`.
2. Pool admin also allowlists `MetricOmmSimpleRouter` so that `alice` can use the router.
3. `bob` (not allowlisted) calls `router.exactInputSingle(pool, ..., recipient=bob, ...)`.
4. The router calls `pool.swap(recipient=bob, ...)` — pool's `msg.sender` is the router.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true`.
6. The swap executes. `bob` receives output tokens despite never being allowlisted.

The configured guard (`allowedSwapper[pool][bob] == false`) is never consulted on the router path. [5](#0-4) [6](#0-5)

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
