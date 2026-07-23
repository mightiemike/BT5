Based on my analysis of the codebase, I have found a valid vulnerability.

---

### Title
SwapAllowlistExtension Gates on Router Address Instead of Actual User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. The pool always sets `sender = msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the actual user. This means the allowlist gates the router's address, not the real swapper. Any pool admin who allowlists the router to support router-mediated swaps inadvertently opens the pool to **all** users, defeating the curated-access guarantee.

### Finding Description

In `MetricOmmPool.swap`, the pool derives `sender` from `msg.sender` and passes it directly to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` encodes this value as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value the pool forwarded — i.e., whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter`, the call chain is:

```
User → MetricOmmSimpleRouter.exactInput*() → pool.swap()
```

Inside `pool.swap()`, `msg.sender` is the **router**, so `sender` forwarded to the extension is the router's address. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates two broken outcomes:

1. **Bypass**: If the pool admin allowlists the router address (the natural step to enable router-mediated swaps), every user — including those explicitly not allowlisted — can swap through the router. The per-user curation is completely defeated.
2. **Broken functionality**: If the pool admin allowlists individual users but not the router, those users cannot use the router at all, even though they are explicitly permitted. The only working path is a direct `pool.swap()` call, which requires the user to implement `IMetricOmmSwapCallback` themselves.

The `DepositAllowlistExtension` does **not** share this flaw because it gates on `owner` (the position owner argument), not `sender` (the payer/caller): [4](#0-3) 

### Impact Explanation

**Critical/High** — direct policy bypass on curated pools. A pool configured with `SwapAllowlistExtension` to restrict trading to a specific set of counterparties (e.g., KYC'd addresses, institutional partners) can be accessed by any unprivileged user simply by routing through the public `MetricOmmSimpleRouter`. This allows unauthorized swaps against LP assets, breaking the core access-control invariant the extension is designed to enforce. [5](#0-4) 

### Likelihood Explanation

**High** — the `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint described in the protocol documentation. Any pool operator who deploys `SwapAllowlistExtension` and also wants users to swap through the standard router will naturally allowlist the router address, triggering the bypass. The flaw requires no special privileges, no malicious setup, and no non-standard tokens. [6](#0-5) 

### Recommendation

The extension must resolve the actual end-user identity rather than the immediate caller of `pool.swap()`. Two approaches:

1. **Pass the real user through `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted encoding convention.
2. **Check `sender` against a router registry and fall through to a user-identity field**: If `sender` is a known trusted router, require the extension payload to carry the real user address and verify it.
3. **Alternatively, gate on `recipient`**: If the pool's design guarantees that `recipient` is always the economic beneficiary of the swap, the extension could check `recipient` instead of `sender`. However, this must be verified against the router's actual argument binding.

The cleanest fix is for the router to forward the originating user address in `extensionData` and for `SwapAllowlistExtension` to decode and check that value when `sender` is a recognized router.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // Alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)  // Router allowlisted to support UX
  - Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)

Execution trace:
  1. Bob → router.exactInputSingle(pool, ...)
  2. router → pool.swap(recipient=Bob, ...)   [msg.sender = router]
  3. pool → _beforeSwap(sender=router, ...)
  4. pool → SwapAllowlistExtension.beforeSwap(sender=router, ...)
  5. Extension checks: allowedSwapper[pool][router] == true  ✓  (passes)
  6. Bob's swap executes against LP assets despite not being allowlisted

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds — allowlist bypassed
``` [3](#0-2) [7](#0-6) [2](#0-1)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L151-177)
```text
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
