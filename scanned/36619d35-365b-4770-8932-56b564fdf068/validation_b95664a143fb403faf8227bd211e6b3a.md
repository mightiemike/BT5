### Title
SwapAllowlistExtension Gates on Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which `MetricOmmPool.swap()` sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, `sender` equals the router contract address, not the actual user. A pool admin who allowlists the router to enable router-based swaps inadvertently opens the allowlist to every user, because any user can call the router and the extension will see the allowlisted router address as the swapper identity.

### Finding Description
In `MetricOmmPool.swap()`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`_beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Inside the extension, `msg.sender` is the pool and `sender` is whoever called the pool. When the router calls the pool, `sender` = router address. The check therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

For a pool admin who wants to support both curated direct swaps and router-based swaps, the natural configuration is to allowlist the router via `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the guard condition `!allowedSwapper[pool][router]` is permanently `false` for every router-mediated call. The per-user check `!allowedSwapper[pool][sender]` is never reached for those calls. Every user, regardless of individual allowlist status, can bypass the guard by routing through the router. [4](#0-3) 

### Impact Explanation
Any user not individually allowlisted can swap on a curated pool by calling `MetricOmmSimpleRouter` instead of the pool directly. The allowlist's purpose — restricting swaps to approved counterparties — is completely defeated. LPs who deployed capital into a curated pool expecting only approved counterparties face unrestricted swap exposure. If the pool's curation was designed to limit adverse selection, regulatory exposure, or front-running, the bypass translates to direct LP principal loss. This matches the allowed impact gate: "High direct loss or curation failure if disallowed users can still trade."

### Likelihood Explanation
The bypass requires the pool admin to allowlist the router. This is a natural and expected configuration for any curated pool that also wants to support the standard periphery router. The admin has no obvious signal that allowlisting the router collapses the per-user allowlist — the two operations appear independent. Likelihood is medium: it affects every curated pool whose admin enables router access, which is a common operational need.

### Recommendation
The extension must gate on the actual end user, not the immediate pool caller. Two viable approaches:

1. Require the router to embed the originating user address in `extensionData`, and have the extension decode and check that address against the allowlist.
2. Add a dedicated `swapOnBehalf(address user, ...)` entry point to the pool that passes `user` as a distinct parameter to extensions, allowing extensions to check the economic actor rather than the intermediary.

The `DepositAllowlistExtension` already demonstrates the correct pattern for deposits — it checks `owner` (the LP position recipient) rather than `sender` (the caller): [5](#0-4) 

The swap allowlist should apply the same principle: gate on the identity that receives the economic benefit of the swap, not the intermediary contract that relays the call.

### Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps.
3. Pool admin calls `setAllowedToSwap(pool, alice, true)` to allowlist alice for direct swaps. Bob is not allowlisted.
4. Bob calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. The router calls `pool.swap(recipient=bob, ...)` — `msg.sender` at the pool is the

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
