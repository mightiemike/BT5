### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` validates the `owner` (position beneficiary) but silently ignores the first parameter `sender` (the actual caller of `pool.addLiquidity`). Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts a caller-supplied `owner` with no requirement that `owner == msg.sender`, any unprivileged address can route a deposit through the adder, name an allowlisted address as `owner`, and have the extension approve the call — depositing tokens into the pool while bypassing the allowlist entirely.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives two address parameters: the first (unnamed, ignored) is `sender` — the immediate caller of `pool.addLiquidity()` — and the second is `owner` — the position beneficiary. [1](#0-0) 

The guard only checks `owner`:

```solidity
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
    revert IMetricOmmPoolActions.NotAllowedToDeposit();
}
``` [2](#0-1) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (the `owner`-overload) accepts an arbitrary `owner` address. Its only validation is `_validateOwner(owner)`, which only rejects `address(0)`: [3](#0-2) [4](#0-3) 

There is no `msg.sender == owner` check. The adder stores `msg.sender` as the `payer` in transient context and passes the caller-supplied `owner` to the pool: [5](#0-4) 

When the pool fires `_beforeAddLiquidity`, it passes `msg.sender` (the adder contract) as `sender` and the caller-supplied address as `owner`: [6](#0-5) 

The extension receives `sender = adder` (ignored) and `owner = allowlistedUser` (checked). Because `allowlistedUser` is on the allowlist, the guard passes, and the unauthorized caller's tokens are pulled into the pool.

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may contribute liquidity (e.g., KYC-gated or private pools). Because the guard checks the position beneficiary (`owner`) rather than the actual depositor (`sender` / `msg.sender` of the adder call), any address — regardless of allowlist status — can deposit tokens into the pool by naming an allowlisted address as `owner`. The pool admin's access-control boundary is broken by an unprivileged path. Tokens from unauthorized depositors enter the pool's accounting, violating the invariant that only allowlisted parties may deposit.

---

### Likelihood Explanation

The attack requires only: (1) knowledge of any allowlisted address (publicly readable from `allowedDepositor`), (2) willingness to pay tokens (which are credited to the allowlisted address's position). No privileged role, flash loan, or special timing is needed. The `MetricOmmPoolLiquidityAdder` is the standard periphery entry point, making this path reachable by any EOA.

---

### Recommendation

**Option A (preferred):** Add `msg.sender == owner` enforcement in `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (the `owner`-overload), so the payer and position owner are always the same address:

```solidity
function addLiquidityExactShares(
    address pool,
    address owner,
    ...
) external payable override returns (...) {
    _validateOwner(owner);
+   if (msg.sender != owner) revert CallerIsNotOwner();
    ...
}
```

**Option B:** Change `DepositAllowlistExtension.beforeAddLiquidity` to check the `sender` parameter instead of `owner`. Note that when the adder is used, `sender` will be the adder contract address, so the adder itself would need to be allowlisted — making Option A the cleaner fix.

---

### Proof of Concept

```solidity
// Pool configured with DepositAllowlistExtension; only `alice` is allowlisted.
// `bob` is NOT allowlisted.

address alice = makeAddr("alice");
address bob   = makeAddr("bob");

// Pool admin allowlists alice only
ext.setAllowedToDeposit(pool, alice, true);

// Bob calls the adder, naming alice as owner
vm.startPrank(bob);
token0.approve(address(adder), type(uint256).max);
token1.approve(address(adder), type(uint256).max);

// Extension checks allowedDepositor[pool][alice] == true → passes
// Bob's tokens are pulled; position credited to alice
adder.addLiquidityExactShares(pool, alice, 0, deltas, max0, max1, "");
vm.stopPrank();

// Bob (unauthorized) successfully deposited into the allowlist-gated pool.
// alice.removeLiquidity() can recover the tokens; bob's funds are gone.
```

### Citations

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L183-196)
```text
  function _addLiquidity(
    address pool,
    address positionOwner,
    uint80 salt,
    LiquidityDelta memory deltas,
    address payer,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) internal returns (uint256 amount0Added, uint256 amount1Added) {
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
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
