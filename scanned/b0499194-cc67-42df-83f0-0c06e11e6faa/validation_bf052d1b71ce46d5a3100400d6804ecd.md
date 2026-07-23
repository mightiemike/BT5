### Title
`DepositAllowlistExtension` validates LP-share recipient (`owner`) instead of actual depositor (`sender`), allowing any caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual on-chain caller who provides tokens) and instead validates the caller-supplied `owner` argument (the LP-share recipient). Because `owner` is a free parameter in `MetricOmmPool.addLiquidity`, any address can bypass the allowlist by passing an already-allowlisted address as `owner`, depositing into a restricted pool without authorization.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
//                  ^^^^^^^^^^  ^^^^^
//                  sender      owner (caller-supplied)
``` [1](#0-0) 

Inside `ExtensionCalling._beforeAddLiquidity`, this becomes the first two positional arguments forwarded to the extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` then **ignores** the first argument (`sender`) entirely — it is unnamed — and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

Because `owner` is a free parameter chosen by the caller of `addLiquidity`, any unauthorized address can call:

```solidity
pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)
```

The extension evaluates `allowedDepositor[pool][allowlistedAddress]` → `true` and permits the call. The unauthorized caller provides the tokens (via the `metricOmmModifyLiquidityCallback` pull), and LP shares are minted to `allowlistedAddress`.

The `MetricOmmPoolLiquidityAdder` makes this even more accessible: its `addLiquidityExactShares(pool, owner, ...)` overload accepts an arbitrary `owner` address (validated only to be non-zero), so any user can invoke the full periphery flow with a spoofed `owner`: [4](#0-3) 

The existing test suite inadvertently confirms the bug: `test_passesWhenDepositorAllowed` passes `address(0)` as `sender` and the allowlisted address as `owner`, demonstrating that the sender identity is irrelevant to the guard: [5](#0-4) 

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may provide liquidity to a pool (e.g., KYC-gated or institutional-only pools). With this bug the guard is completely inoperative: any unprivileged address can deposit arbitrary liquidity, manipulate bin positions and the pool cursor, and force LP shares onto allowlisted addresses without their consent. This constitutes a broken core pool functionality (unauthorized liquidity injection) and an admin-boundary break (an unprivileged path defeats a pool-admin-configured security control), both of which are within the Metric OMM Allowed Impact Gate.

---

### Likelihood Explanation

Exploitation requires no special privileges, no flash loan, and no complex setup. Any EOA or contract can call `pool.addLiquidity` directly or route through `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` with any allowlisted address as `owner`. The only prerequisite is knowing one allowlisted address, which is readable from the public `allowedDepositor` mapping.

---

### Recommendation

Swap the validated parameter from `owner` to `sender` in `beforeAddLiquidity`:

```solidity
// Before (buggy):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// After (fixed):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
``` [3](#0-2) 

The `setAllowedToDeposit` / `isAllowedToDeposit` public API and the `allowedDepositor` mapping key semantics should be updated to document that the keyed address is the **caller** (`msg.sender` of `addLiquidity`), not the LP-share recipient.

---

### Proof of Concept

```
Setup
─────
1. Admin deploys pool with DepositAllowlistExtension configured.
2. Admin calls extension.setAllowedToDeposit(pool, trustedUser, true).
   → allowedDepositor[pool][trustedUser] = true
   → attacker is NOT in the allowlist.

Attack
──────
3. attacker calls:
     pool.addLiquidity(
         owner        = trustedUser,   // allowlisted address — freely chosen
         salt         = 0,
         deltas       = <any valid delta>,
         callbackData = <pay callback>,
         extensionData = ""
     )

4. Pool calls _beforeAddLiquidity(attacker, trustedUser, ...).

5. DepositAllowlistExtension.beforeAddLiquidity receives:
     sender (ignored) = attacker
     owner (checked)  = trustedUser
   Evaluates: allowedDepositor[pool][trustedUser] == true → no revert.

6. Pool proceeds; attacker's tokens are pulled via callback;
   LP shares are minted to trustedUser.

Result
──────
- attacker deposited into a restricted pool without being allowlisted.
- trustedUser received unsolicited LP shares.
- Pool bin state was modified by an unauthorized party.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
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

**File:** metric-periphery/test/extensions/DepositAllowlistSubExtension.t.sol (L34-41)
```text
  function test_passesWhenDepositorAllowed() public {
    vm.prank(admin);
    extension.setAllowedToDeposit(address(pool), depositor, true);

    vm.prank(address(pool));
    LiquidityDelta memory emptyDelta = LiquidityDelta({binIdxs: new int256[](0), shares: new uint256[](0)});
    extension.beforeAddLiquidity(address(0), depositor, 0, emptyDelta, "");
  }
```
