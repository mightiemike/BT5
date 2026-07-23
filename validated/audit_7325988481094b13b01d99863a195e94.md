Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`) and instead validates `owner` (the LP position recipient), which is a freely chosen argument with no constraint tying it to the caller. Because `addLiquidity` enforces no relationship between `msg.sender` and `owner` (unlike `removeLiquidity`, which requires `msg.sender == owner`), any unprivileged address can name an already-allowlisted address as `owner` and pass the guard unconditionally. The deposit allowlist is completely neutralized.

## Finding Description
`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its admin API uses the term `depositor` throughout:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L13-14
mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
mapping(address pool => bool) public allowAllDepositors;

// L18-19
function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
```

However, the hook implementation at L32-42 discards the first parameter (`sender`) and checks the second (`owner`):

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`MetricOmmPool.addLiquidity` (L191) calls `_beforeAddLiquidity(msg.sender, owner, ...)` where `owner` is a caller-supplied argument with no binding to `msg.sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

Contrast with `removeLiquidity` (L206), which enforces `msg.sender == owner` before calling the hook — `addLiquidity` has no equivalent guard.

`ExtensionCalling._beforeAddLiquidity` (L95-98) faithfully forwards both `sender` and `owner` to the extension, so the misbinding is entirely within `DepositAllowlistExtension` itself.

**Exploit path:**
1. Pool is deployed with `DepositAllowlistExtension`; `allowAllDepositors[pool] = false`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)`. Bob is not allowlisted.
3. Bob calls `pool.addLiquidity(owner = alice, salt, deltas, callbackData, extensionData)`.
4. Pool calls `_beforeAddLiquidity(sender = bob, owner = alice, ...)`.
5. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
6. `LiquidityLib.addLiquidity` executes; Bob's tokens are pulled via callback; Alice's position is credited.
7. Bob has deposited into a pool he is not authorized to access.

## Impact Explanation
The deposit allowlist — the sole purpose of `DepositAllowlistExtension` — is completely bypassed. Any unauthorized address can supply tokens to a restricted pool by naming any allowlisted address as `owner`. The unauthorized caller provides the tokens (via the `addLiquidity` callback path) while the allowlisted address receives the LP shares. Pools relying on this guard for institutional or compliance-gated liquidity have no effective access control on deposits. This constitutes a broken core pool access-control mechanism with direct fund-flow impact (unauthorized token deposits into restricted pools).

## Likelihood Explanation
Exploitation requires no special privilege. Any address that can call `addLiquidity` on the pool can exploit this. The only prerequisite is knowing one allowlisted address, which is discoverable from on-chain `AllowedToDepositSet` events emitted at L20. The attack is a single transaction and is fully repeatable.

## Recommendation
Replace the `owner` check with `sender` in `beforeAddLiquidity`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This aligns the runtime check with the documented intent ("gate by depositor address") and with the naming of the admin API (`depositor`, `allowedDepositor`).

## Proof of Concept

```solidity
// Foundry test sketch
function test_depositAllowlistBypass() public {
    // Setup: pool with DepositAllowlistExtension, allowAllDepositors = false
    // alice is allowlisted, bob is not
    vm.prank(poolAdmin);
    extension.setAllowedToDeposit(address(pool), alice, true);

    // Bob calls addLiquidity with owner = alice
    vm.prank(bob);
    pool.addLiquidity(
        alice,       // owner = allowlisted address
        salt,
        deltas,
        callbackData,
        extensionData
    );
    // Succeeds: bob deposited tokens, alice received LP shares
    // Bob was never allowlisted — allowlist fully bypassed
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-19)
```text
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

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
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

**File:** metric-core/contracts/MetricOmmPool.sol (L199-212)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
      _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
  }
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
