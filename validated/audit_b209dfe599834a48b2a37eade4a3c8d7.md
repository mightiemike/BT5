Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` validates `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and validates only `owner` (a free caller-supplied parameter). Because `addLiquidity` imposes no `msg.sender == owner` constraint, any address not on the allowlist can bypass the guard by naming an allowlisted address as `owner`, paying the tokens themselves via callback, and receiving nothing while the allowlisted address receives an unsolicited position.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as a separate argument into the extension hook:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both to the extension unchanged:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives both but drops `sender` (unnamed first parameter) and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [3](#0-2) 

`addLiquidity` has no `msg.sender == owner` guard (unlike `removeLiquidity` which enforces it at L206): [4](#0-3) 

`SwapAllowlistExtension.beforeSwap`, by contrast, correctly checks `sender`:

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [5](#0-4) 

The extension's own NatSpec and mapping names confirm the intent is to gate the actual token provider: the contract is titled "Gates `addLiquidity` by depositor address, per pool", the mapping is `allowedDepositor`, and the setter is `setAllowedToDeposit(pool, depositor, allowed)`. [6](#0-5) 

**Exploit path:**
1. Pool admin allowlists only Alice: `setAllowedToDeposit(pool, Alice, true)`.
2. Bob (not allowlisted) calls `pool.addLiquidity(owner = Alice, salt, deltas, callbackData, "")` directly.
3. Extension receives `sender = Bob`, `owner = Alice`; checks `allowedDepositor[pool][Alice]` → **passes**.
4. Bob's `metricOmmModifyLiquidityCallback` is invoked; Bob transfers tokens into the pool.
5. Pool records the position under Alice; Bob has successfully interacted with the restricted pool.

The wrong value is the `extension decision` — the guard passes for an identity (`owner = Alice`) that is not the token provider, while the actual token provider (`sender = Bob`) is never checked.

## Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting which addresses may provide liquidity (e.g., KYC/AML compliance, institutional-only pools). Because the guard checks the position beneficiary (`owner`) rather than the token provider (`sender`), the restriction is entirely ineffective against a direct pool call. Any non-allowlisted address can provide tokens to the pool, affect bin totals and pool state, and force an unsolicited position onto an allowlisted address. This is an **Admin-boundary break**: a factory/pool-admin-configured guard is bypassed by an unprivileged caller path with no special setup required.

## Likelihood Explanation

No special privilege is required; any EOA or contract can call `pool.addLiquidity` directly. The attacker only needs to know one allowlisted address, which is publicly readable from the `allowedDepositor` mapping or emitted `AllowedToDepositSet` events. The `owner != msg.sender` pattern is explicitly documented and tested in the codebase (`test_exactShares_canAddOnBehalfOfAnotherOwner`), making the bypass path well-known. [7](#0-6) 

## Recommendation

Change `DepositAllowlistExtension.beforeAddLiquidity` to validate `sender` (the actual token provider) instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension`:

```solidity
// fixed:
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

Also update the `IMetricOmmPoolActions` NatSpec at L147 (which currently says "owner must pass allowlist") to reflect that `sender` is the gated identity.

## Proof of Concept

```solidity
// Pool admin allowlists only Alice
depositExtension.setAllowedToDeposit(address(pool), alice, true);

// Bob (not allowlisted) calls addLiquidity directly, naming Alice as owner
// Bob implements IMetricOmmModifyLiquidityCallback to pay the tokens
vm.prank(bob);
pool.addLiquidity(
    alice,           // owner = allowlisted Alice → guard passes on owner check
    salt,
    deltas,
    abi.encode(...), // Bob's callback pays the tokens
    ""
);

// Result: Bob's tokens are in the pool; Alice has an unwanted position
// The deposit allowlist was bypassed — Bob (not allowlisted) interacted with the restricted pool
uint256 aliceShares = positionBinShares(address(pool), alice, salt, binIdx);
assertGt(aliceShares, 0); // Alice has shares she never requested
```

The existing test suite in `DepositAllowlistSubExtension.t.sol` always passes `address(0)` as the first (sender) argument and `depositor` as the second (owner), so it never exercises the sender-vs-owner separation and does not catch this bug. [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-40)
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
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-38)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
```

**File:** metric-periphery/test/MetricOmmPoolLiquidityAdder.t.sol (L211-220)
```text
  function test_exactShares_canAddOnBehalfOfAnotherOwner() public {
    LiquidityDelta memory d = _deltaAbovePrice(4, 10_000);
    address bob = makeAddr("bob");

    vm.prank(alice);
    helper.addLiquidityExactShares(address(pool), bob, 1, d, type(uint256).max, type(uint256).max, "");

    uint256 bobShares = stateView.positionBinShares(address(pool), bob, 1, int8(4));
    assertGt(bobShares, 0);
  }
```

**File:** metric-periphery/test/extensions/DepositAllowlistSubExtension.t.sol (L27-41)
```text
  function test_revertsWhenDepositorNotAllowed() public {
    vm.prank(address(pool));
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToDeposit.selector);
    LiquidityDelta memory emptyDelta = LiquidityDelta({binIdxs: new int256[](0), shares: new uint256[](0)});
    extension.beforeAddLiquidity(address(0), depositor, 0, emptyDelta, "");
  }

  function test_passesWhenDepositorAllowed() public {
    vm.prank(admin);
    extension.setAllowedToDeposit(address(pool), depositor, true);

    vm.prank(address(pool));
    LiquidityDelta memory emptyDelta = LiquidityDelta({binIdxs: new int256[](0), shares: new uint256[](0)});
    extension.beforeAddLiquidity(address(0), depositor, 0, emptyDelta, "");
  }
```
