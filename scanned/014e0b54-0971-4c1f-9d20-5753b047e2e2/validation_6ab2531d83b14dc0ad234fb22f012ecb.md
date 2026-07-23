### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Non-Allowlisted Addresses to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` gates `addLiquidity` by checking the `owner` parameter (the LP-position recipient) rather than the `sender` parameter (the address actually calling `addLiquidity` and providing the tokens). Because `sender` and `owner` are distinct fields in the extension interface, any non-allowlisted address can bypass the deposit guard by specifying an allowlisted address as `owner`.

---

### Finding Description

The pool calls `beforeAddLiquidity` with two separate actor fields:

- `sender` — the address that called `pool.addLiquidity()` (the actual depositor, the one transferring tokens)
- `owner` — the address that will own the resulting LP position (may be any address)

`DepositAllowlistExtension.beforeAddLiquidity` ignores `sender` entirely and only checks `owner`: [1](#0-0) 

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

The first parameter (`sender`) is silently discarded (unnamed `address`). The guard passes as long as `owner` is allowlisted, regardless of who is actually calling and funding the deposit.

Compare with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the actual swapper): [2](#0-1) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The interface explicitly separates `sender` from `owner` for exactly this reason: [3](#0-2) 

And `ExtensionCalling._beforeAddLiquidity` faithfully forwards both distinct addresses to the extension: [4](#0-3) 

The deposit allowlist admin configures per-depositor permissions via `setAllowedToDeposit(pool, depositor, true)`, where the semantic intent is clearly to gate the depositing actor, not the position recipient: [5](#0-4) 

---

### Impact Explanation

The deposit allowlist guard is completely ineffective at restricting the actual depositor. Any non-allowlisted address can deposit tokens into a restricted pool by passing any allowlisted address as `owner`. The non-allowlisted address provides the funds; the allowlisted address receives the LP position. The pool admin's access-control configuration is silently bypassed with no revert. This breaks the core invariant of the extension: that only allowlisted addresses may add liquidity to the pool.

---

### Likelihood Explanation

Exploitation requires only knowing one allowlisted address (which is publicly readable from `allowedDepositor`) and calling `addLiquidity` directly on the pool (or through any router that accepts a caller-specified `owner`). No privileged access, special tokens, or complex setup is needed. Any non-allowlisted address can trigger this at will.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual depositor) instead of `owner`, consistent with how `SwapAllowlistExtension` handles `beforeSwap`:

```solidity
// Before (wrong — checks position recipient, not depositor)
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// After (correct — checks actual caller providing funds)
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
```

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` configured.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — only `alice` is allowlisted.
3. Non-allowlisted `bob` calls `pool.addLiquidity(owner=alice, ...)` directly.
4. Pool calls `extension.beforeAddLiquidity(sender=bob, owner=alice, ...)`.
5. The check evaluates `allowedDepositor[pool][alice]` → `true` → **no revert**.
6. Bob's tokens are transferred into the pool; the LP position is minted to `alice`.
7. Bob has successfully deposited into a pool he is not allowlisted for. The deposit allowlist is bypassed.

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-21)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L14-21)
```text
  function beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) external returns (bytes4);

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
