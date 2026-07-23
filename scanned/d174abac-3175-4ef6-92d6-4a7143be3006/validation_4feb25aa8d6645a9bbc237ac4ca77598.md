### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender`, so the allowlist checks the **router's address** rather than the **actual user's address**. If the pool admin allowlists the router (the natural configuration for letting allowlisted users reach the pool via the router), any non-allowlisted user can bypass the per-pool swap gate entirely.

---

### Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender` to the hook dispatcher.**

In `MetricOmmPool.swap()`:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap(); the router when routed
    recipient,
    ...
);
``` [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` forwards that value unchanged as the `sender` argument to every configured extension.** [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`, where `sender` is the router, not the user.**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

The allowlist mapping is keyed `allowedSwapper[pool][swapper]`. When the router calls `pool.swap()`, `sender` = router address, so the check becomes `allowedSwapper[pool][router]`. The actual user's address is never consulted.

**The mismatch (M-07 analog):**

| Step | M-07 (Biconomy) | Metric OMM analog |
|---|---|---|
| Guard check | `amount < maxAmount` (passes) | `allowedSwapper[pool][router]` (passes — router is allowlisted) |
| Actual operation | uses `amount + reward` (larger value) | swap executed on behalf of non-allowlisted user |
| Result | executor call reverts, user loses funds | intended user-level gate is bypassed |

In both cases the guard checks one value/entity while the operation is attributed to a different one, breaking the invariant the guard was meant to enforce.

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., institutional traders, KYC'd addresses) is fully bypassed. Any non-allowlisted user can call `MetricOmmSimpleRouter` targeting the pool and trade freely. LP funds on the curated pool are exposed to unauthorized traders, enabling adverse selection and direct LP principal loss. This matches the **"Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path"** and **"Broken core pool functionality causing loss of funds"** impact gates.

---

### Likelihood Explanation

The bypass is reachable whenever:
1. A pool is deployed with `SwapAllowlistExtension` active.
2. The pool admin allowlists the router (the natural configuration so that allowlisted users can reach the pool via the router rather than calling it directly).

Both conditions are expected in normal production use. No privileged attacker role is required — any public user can call the router.

---

### Recommendation

The extension must check the **economic actor** (the end user), not the **transport layer** (the router). Two viable approaches:

1. **Encode the original caller in `extensionData`**: The router encodes `msg.sender` (the user) into `extensionData` before forwarding to the pool. `SwapAllowlistExtension.beforeSwap` decodes and checks that address instead of `sender`.

2. **Check both `sender` and a decoded user address**: If `extensionData` is non-empty, decode and check the user; otherwise fall back to `sender`. This preserves backward compatibility for direct pool calls.

The `DepositAllowlistExtension` does **not** share this flaw because it checks `owner` (the LP position owner, passed explicitly by the caller), not `sender`. [4](#0-3) 

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` and `setAllowedToSwap(pool, router, true)` (router allowlisted so Alice can use it).
3. Bob (non-allowlisted) calls `MetricOmmSimpleRouter.exactInput(...)` targeting the pool.
4. Router calls `pool.swap(recipient, ...)` — `msg.sender` inside the pool = router.
5. Pool calls `_beforeSwap(router, ...)`.
6. `SwapAllowlistExtension` evaluates `allowedSwapper[pool][router]` → `true`.
7. Bob's swap executes. The allowlist never checked Bob's address. [5](#0-4) [6](#0-5)

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
