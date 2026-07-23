### Title
`DepositAllowlistExtension` gates on `owner` instead of `sender`, allowing any caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` checks the caller-supplied `owner` (position owner) against the per-pool allowlist instead of `sender` (the actual depositor who provides tokens via callback). Because `owner` is an unvalidated argument to `MetricOmmPool.addLiquidity`, any unauthorized address can bypass the deposit allowlist by passing any allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` with no validation and forwards both `msg.sender` (as `sender`) and `owner` to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` passes `sender = msg.sender` and the raw `owner` argument to every configured extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the first parameter (`sender`) and gates only on `owner`: [3](#0-2) 

The sibling `SwapAllowlistExtension.beforeSwap` demonstrates the correct pattern — it checks `sender` (the actual swapper), not the second address argument: [4](#0-3) 

The inconsistency is structural: `owner` is a free caller-supplied parameter with no on-chain constraint tying it to `msg.sender`. The extension's description states it "Gates `addLiquidity` by depositor address", but the depositor (token provider) is `sender`, not `owner`. [5](#0-4) 

---

### Impact Explanation

Any address not on the allowlist can deposit into a curated pool by calling:

```
pool.addLiquidity(allowedLP, salt, deltas, callbackData, extensionData)
```

The extension evaluates `allowedDepositor[pool][allowedLP]` — which is `true` — and does not revert. The pool then pulls tokens from the unauthorized caller via the swap callback and credits the position to `allowedLP`. The pool admin's curation policy (restricting who may deposit) is completely defeated. The unauthorized caller loses their tokens to `allowedLP`'s position, but the allowlist guard — the sole mechanism protecting pool curation — fails open for every deposit action.

This is an admin-boundary break: an unprivileged path bypasses a configured access-control guard on a core pool action.

---

### Likelihood Explanation

- No special role or privilege is required; any EOA or contract can call `addLiquidity` with an arbitrary `owner`.
- The bypass requires only knowing one allowlisted address (publicly readable from `allowedDepositor`).
- The pool's `removeLiquidity` enforces `msg.sender == owner`, so the attacker cannot recover the deposited tokens — but the allowlist invariant is still broken on every such call. [6](#0-5) 

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual token provider) instead of `owner`:

```solidity
function beforeAddLiquidity(address sender, address /*owner*/, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This mirrors the correct pattern already used in `SwapAllowlistExtension`.

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` as a `beforeAddLiquidity` hook.
2. Pool admin calls `setAllowedToDeposit(pool, allowedLP, true)` — only `allowedLP` is permitted.
3. `attacker` (not on the allowlist) calls:
   ```
   pool.addLiquidity(allowedLP, salt, deltas, callbackData, extensionData)
   ```
4. Pool calls `_beforeAddLiquidity(attacker /*sender*/, allowedLP /*owner*/, ...)`.
5. Extension evaluates `allowedDepositor[pool][allowedLP]` → `true` → does **not** revert.
6. Pool executes `LiquidityLib.addLiquidity` with `owner = allowedLP`, pulling tokens from `attacker` via callback.
7. Position is credited to `allowedLP`; `attacker` has deposited into a pool that was supposed to reject them.
8. The deposit allowlist has been bypassed with zero privileges. [3](#0-2) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L199-206)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L11-19)
```text
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
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
