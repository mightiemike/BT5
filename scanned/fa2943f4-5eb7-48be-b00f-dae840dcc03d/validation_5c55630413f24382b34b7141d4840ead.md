### Title
Deposit Allowlist Checks `owner` Instead of `sender`, Allowing Any Unprivileged Address to Bypass the Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` parameter and gates on `owner` instead. Because `owner` is a free caller-supplied argument to `addLiquidity`, any address — regardless of allowlist status — can bypass the deposit guard by nominating an already-allowlisted address as the position owner.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook:

- `sender` = `msg.sender` — the address that actually calls `addLiquidity` and provides tokens via the swap callback.
- `owner` = a caller-supplied parameter — the address that will own the resulting LP position. [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity`, however, discards `sender` entirely (unnamed `address,`) and checks only `owner`: [3](#0-2) 

The contract's own NatSpec and the setter's parameter name both declare the intent is to gate by **depositor** address: [4](#0-3) [5](#0-4) 

Because `owner` is a free argument chosen by the caller, any non-allowlisted address can pass the guard by setting `owner` to any address that is already on the allowlist.

---

### Impact Explanation

**Deposit allowlist is completely ineffective.** A non-allowlisted attacker calls:

```solidity
pool.addLiquidity(
    owner    = allowlistedAddress,   // passes the guard
    salt     = ...,
    deltas   = ...,                  // attacker-chosen bins/shares
    callbackData = ...,
    extensionData = ...
);
```

The `beforeAddLiquidity` check passes because `allowedDepositor[pool][allowlistedAddress]` is `true`. The attacker's tokens are pulled via the callback and credited to `allowlistedAddress`'s position. The allowlisted owner cannot prevent this; `removeLiquidity` enforces `msg.sender == owner`, so only the allowlisted owner can withdraw those shares — the attacker's tokens are permanently locked in the pool under someone else's position.

Concrete fund-impacting consequences:

1. **Allowlist guard is nullified** — the pool admin's access control is bypassed by any unprivileged caller, violating the admin-boundary invariant.
2. **Forced LP exposure** — the allowlisted owner receives unwanted shares in attacker-chosen bins (potentially illiquid or far-from-mid bins), distorting their risk profile without consent.
3. **Pool liquidity manipulation** — an attacker can concentrate liquidity in specific bins to skew the pool's effective spread and bin-crossing behavior, then profit via a subsequent swap (if no swap allowlist is active), extracting value from existing LPs.

---

### Likelihood Explanation

- Requires no special privilege — any EOA or contract can call `addLiquidity`.
- The only prerequisite is knowing one allowlisted address (trivially discoverable from `AllowedToDepositSet` events).
- The attacker bears the cost of the deposited tokens, but can recover value through a subsequent swap if the pool lacks a `SwapAllowlistExtension`.
- Likelihood is **High** for pools that rely solely on `DepositAllowlistExtension` for access control.

---

### Recommendation

Replace the ignored first parameter with the actual `sender` check:

```solidity
function beforeAddLiquidity(
    address sender,   // ← use this, not owner
    address,
    uint80,
    LiquidityDelta calldata,
    bytes calldata
) external view override returns (bytes4) {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This matches the stated contract invariant ("Gates `addLiquidity` by depositor address") and the `setAllowedToDeposit(address depositor, ...)` API.

---

### Proof of Concept

```solidity
// Setup: pool with DepositAllowlistExtension; only `alice` is allowlisted.
// `bob` is NOT allowlisted.

// Bob bypasses the allowlist by nominating alice as owner:
vm.startPrank(bob);
token0.approve(address(pool), type(uint256).max);
token1.approve(address(pool), type(uint256).max);

pool.addLiquidity(
    alice,          // owner — passes allowlist check (alice is allowlisted)
    0,              // salt
    deltas,         // attacker-chosen liquidity delta
    callbackData,
    ""
);
vm.stopPrank();

// Result: bob's tokens are now in alice's LP position.
// The allowlist check never evaluated bob's address.
// alice has unwanted LP exposure; bob's tokens are locked under alice's position.
assertEq(extension.isAllowedToDeposit(address(pool), bob), false); // bob is NOT allowed
// Yet the deposit succeeded — guard bypassed.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-195)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L11-13)
```text
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-20)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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
