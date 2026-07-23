### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any non-allowlisted address to bypass the deposit gate — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter and gates on `owner` instead. Because `owner` is a free caller-supplied argument with no binding to `msg.sender` in `MetricOmmPool.addLiquidity`, any non-allowlisted address can pass the check by naming an allowlisted address as `owner`.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives two address parameters: `sender` (the actual caller of `addLiquidity`, i.e. the economic payer) and `owner` (the position recipient, freely chosen by the caller). The function discards `sender` entirely and checks only `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

The pool passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to the extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [2](#0-1) 

Both are forwarded to the extension via `abi.encodeCall`: [3](#0-2) 

`MetricOmmPool.addLiquidity` imposes no constraint that `owner == msg.sender`: [4](#0-3) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (with explicit owner) also explicitly supports `owner != msg.sender` and only validates `owner != address(0)`: [5](#0-4) 

The contract's own NatSpec states the intent: **"Gates `addLiquidity` by depositor address"** — meaning the `sender`/payer, not the position recipient. The mapping is even named `allowedDepositor`, confirming the intended subject is the depositing party: [6](#0-5) 

---

### Impact Explanation

**Attack path:**

1. Pool is configured with `DepositAllowlistExtension` in `BEFORE_ADD_LIQUIDITY_ORDER`.
2. Alice (`allowedDepositor[pool][Alice] = true`) is allowlisted; Bob is not.
3. Bob calls `pool.addLiquidity(owner=Alice, salt, deltas, callbackData, extensionData)` directly, or via `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner=Alice, ...)`.
4. The extension checks `allowedDepositor[pool][Alice]` → `true` → hook passes.
5. LP shares are minted to Alice's position; Bob's tokens are pulled in the callback.
6. Bob cannot recover his tokens: `removeLiquidity` enforces `msg.sender == owner` (Alice), so Bob's funds are permanently locked in Alice's position.

**Broken invariant:** The pool admin's deposit gate is completely ineffective. Any non-allowlisted address can deposit into a restricted pool by naming any allowlisted address as `owner`. The allowlist protects nothing.

**Fund loss:** The non-allowlisted depositor loses their tokens permanently (transferred to pool, LP shares credited to the named allowlisted owner, irrecoverable by the payer).

---

### Likelihood Explanation

- Exploitable by any address with knowledge of one allowlisted address (publicly readable from `allowedDepositor`).
- No privileged access, no special setup, no malicious token required.
- Directly callable on-chain via `MetricOmmPool.addLiquidity` or `MetricOmmPoolLiquidityAdder`.

---

### Recommendation

Check `sender` (the actual depositor/payer) instead of `owner` in `beforeAddLiquidity`:

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

If the intent is to gate both the payer and the position recipient, check both `sender` and `owner`.

---

### Proof of Concept

```solidity
// Foundry integration test (pseudo-code)
function test_nonAllowlistedBypassesDepositGate() public {
    // Setup: pool with DepositAllowlistExtension in BEFORE_ADD_LIQUIDITY_ORDER
    // Alice is allowlisted, Bob is not
    depositExtension.setAllowedToDeposit(address(pool), alice, true);
    assertFalse(depositExtension.isAllowedToDeposit(address(pool), bob));

    // Bob calls addLiquidity with alice as owner
    LiquidityDelta memory d = _deltaAbovePrice(4, 10_000);
    vm.prank(bob); // non-allowlisted sender
    // Does NOT revert — extension checks owner=alice (allowlisted), ignores sender=bob
    pool.addLiquidity(alice, 1, d, abi.encode(KIND_PAY), "");

    // Alice has LP shares, Bob's tokens are gone
    uint256 aliceShares = stateView.positionBinShares(address(pool), alice, 1, int8(4));
    assertGt(aliceShares, 0); // allowlist bypassed
    // Bob cannot call removeLiquidity(alice, ...) — NotPositionOwner
}
```

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L11-13)
```text
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
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

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```
