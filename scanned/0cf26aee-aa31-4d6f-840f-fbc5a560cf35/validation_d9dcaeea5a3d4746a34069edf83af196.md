Based on my investigation of the codebase, I found a concrete wrong-actor binding vulnerability in the swap allowlist extension.

---

### Title
`SwapAllowlistExtension.beforeSwap` gates on the direct pool caller (`sender`) rather than the originating user, allowing any user to bypass a curated pool's per-user swap allowlist by routing through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` enforces its per-user allowlist by checking the `sender` argument, which is `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` (a public periphery contract) calls `pool.swap()`, `sender` equals the router's address, not the originating user. A pool admin who allowlists the router to enable router-based swaps inadvertently grants every user on the router the same permission, completely defeating the per-user curation the extension was deployed to enforce.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes and dispatches this `sender` to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter` is the supported public periphery path that calls `pool.swap()` on behalf of users. When it does so, `sender` = router address. A pool admin who wants to support router-based swaps must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every user who routes through it — the per-user allowlist is completely bypassed.

The `DepositAllowlistExtension` does not share this flaw: it gates by `owner` (the position beneficiary), which is correctly the economic actor regardless of who calls `addLiquidity`: [4](#0-3) 

The pool's own NatSpec confirms the operator pattern for deposits ("msg.sender pays but need not equal owner"), but no equivalent user-forwarding mechanism exists for swaps: [5](#0-4) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict swaps to a specific set of users (e.g., KYC-verified addresses, institutional counterparties) loses all enforcement the moment the router is allowlisted. Any unpermissioned user can call `MetricOmmSimpleRouter` and execute swaps against the pool's liquidity. This constitutes:

- **Broken core pool functionality**: the allowlist guard silently fails open for all router-routed swaps.
- **Direct LP fund impact**: LPs deposited into a curated pool under the assumption that only vetted counterparties could trade against them; unauthorized swaps expose them to adversarial or uninformed order flow they explicitly opted out of.
- **Pool insolvency risk**: if the allowlist was protecting against specific trading patterns (e.g., large directional swaps that drain a bin), the bypass enables exactly those patterns.

---

### Likelihood Explanation

The trigger is a routine, expected administrative action: allowlisting the official periphery router so that users can interact with the pool through the standard supported path. No malicious setup is required. Any user who knows the router address can exploit the bypass immediately after the router is allowlisted. The router is a public, permissionless contract in the periphery layer. [6](#0-5) 

---

### Recommendation

The extension must identify the originating user, not the direct pool caller. Two sound approaches:

1. **Require the router to forward the user identity via `extensionData`**: the extension reads the originating user address from `extensionData` when `sender` is a known router, and checks that address against the allowlist. The router must be trusted to populate this field honestly (enforced by the pool's reentrancy guard and callback model).

2. **Check `sender` only when `sender` is not a registered router**: maintain a registry of trusted routers in the extension; when `sender` is a router, require the actual user address to be passed in `extensionData` and verified; when `sender` is a direct caller, check `sender` as today.

The `DepositAllowlistExtension` pattern (checking `owner`, the economic beneficiary) is the correct model and should be mirrored for swaps by threading the originating user through the router call.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is meant to swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — to allow Alice to use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.swap(pool, ...)`.
5. The router calls `pool.swap(...)` with itself as `msg.sender`.
6. `SwapAllowlistExtension.beforeSwap` receives `sender = router`.
7. `allowedSwapper[pool][router] == true` → check passes.
8. Bob's swap executes against LP liquidity he was never meant to access. [3](#0-2) [1](#0-0)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-14)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L111-113)
```text
  /// @notice Swap allowlist rejected `msg.sender`.
  /// @dev Only `swap` checks this when `SWAP_ALLOWLIST_PROVIDER` is set; `simulateSwapAndRevert` does not, so a passing simulation does not imply an allowed live swap.
  error NotAllowedToSwap();
```
