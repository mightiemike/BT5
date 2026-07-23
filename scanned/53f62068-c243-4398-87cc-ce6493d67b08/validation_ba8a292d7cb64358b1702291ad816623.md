### Title
`DepositAllowlistExtension::beforeAddLiquidity` checks `owner` instead of the actual token payer, allowing non-allowlisted actors to bypass the deposit guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is designed to gate `addLiquidity` by depositor address. However, its `beforeAddLiquidity` hook validates the `owner` (position recipient) rather than the actual token payer (`msg.sender` of the original call). Because `MetricOmmPoolLiquidityAdder` separates `owner` from `payer`, any non-allowlisted actor can bypass the deposit guard by supplying an allowlisted address as `owner`.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives the position `owner` as its second argument and gates on it: [1](#0-0) 

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

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (the owner-overload) explicitly separates `owner` from `payer`: `owner` is the position recipient supplied by the caller, while `payer` is always hardcoded to `msg.sender`: [2](#0-1) 

```solidity
function addLiquidityExactShares(
    address pool,
    address owner,   // ← position recipient, caller-controlled
    ...
) external payable override returns (...) {
    _validateOwner(owner);   // only checks != address(0)
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
    //                                              ^^^^^^^^^^
    //                                              payer = actual token source
}
```

`_validateOwner` only rejects `address(0)`: [3](#0-2) 

The transient pay context stores `payer = msg.sender` (the real depositor), and the callback pulls tokens from that address: [4](#0-3) 

```solidity
if (amount0Delta > 0) pay(token0, payer, msg.sender, amount0Delta);
if (amount1Delta > 0) pay(token1, payer, msg.sender, amount1Delta);
```

`pay` calls `safeTransferFrom(payer, recipient, value)` when `payer != address(this)`: [5](#0-4) 

The allowlist check therefore validates the **position recipient** (`owner`), not the **token source** (`payer = msg.sender`). These are two independent addresses.

---

### Impact Explanation

A non-allowlisted actor Bob can bypass the deposit allowlist entirely:

1. Bob identifies any allowlisted address Alice (`allowedDepositor[pool][alice] == true`).
2. Bob calls `addLiquidityExactShares(pool, alice, salt, deltas, max0, max1, data)`.
3. The pool invokes `beforeAddLiquidity(LiquidityAdder, alice, ...)` on the extension.
4. The extension checks `allowedDepositor[pool][alice]` → `true` → hook passes.
5. The pool credits LP shares to Alice; the callback pulls tokens from Bob.

Result: Bob (non-allowlisted) successfully deposits into a restricted pool. The pool admin's access-control invariant — "only allowlisted addresses may deposit" — is completely broken. Additionally, Alice receives LP shares she never requested, which can be used to grief her (e.g., forcing unwanted pool exposure or complicating her tax/accounting position).

**Severity: Medium** — the deposit allowlist guard is rendered entirely ineffective; no direct loss of existing LP principal, but the core access-control mechanism is broken and any non-allowlisted actor can participate in a restricted pool.

---

### Likelihood Explanation

- Requires no special privilege; any external actor can call `addLiquidityExactShares` with an arbitrary `owner`.
- The only prerequisite is knowing one allowlisted address (publicly readable from `allowedDepositor`).
- Exploitable on every pool that uses `DepositAllowlistExtension` with a non-open allowlist.

---

### Recommendation

The hook must validate the **actual depositor** (the address whose tokens are pulled), not the position recipient. Since the pool's `beforeAddLiquidity` hook receives the pool's direct caller as its first (currently unnamed/ignored) argument, and the real end-user identity must be threaded through `extensionData` or a separate mechanism, the simplest correct fix is:

**Option A — Check the pool's direct caller (the router/adder) and require it to be allowlisted, then enforce that the adder always equals `msg.sender` for direct deposits.** This requires the allowlist to allowlist the router, which defeats per-user gating.

**Option B (preferred) — Pass the real payer address in `extensionData` and verify it in the hook.** The `LiquidityAdder` encodes `payer = msg.sender` into `extensionData`; the extension decodes and checks it:

```solidity
function beforeAddLiquidity(address, address, uint80, LiquidityDelta calldata, bytes calldata extData)
    external view override returns (bytes4)
{
    address depositor = abi.decode(extData, (address));
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][depositor]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

---

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true
  allowedDepositor[pool][bob]   = false  (Bob is NOT allowlisted)

Attack:
  vm.startPrank(bob);
  token0.approve(address(liquidityAdder), MAX);
  token1.approve(address(liquidityAdder), MAX);

  // Bob sets owner = alice (allowlisted), payer = bob (msg.sender)
  liquidityAdder.addLiquidityExactShares(
      pool,
      alice,   // owner — passes allowlist check
      salt,
      deltas,
      max0,
      max1,
      ""
  );
  vm.stopPrank();

  // Result:
  // - beforeAddLiquidity checked allowedDepositor[pool][alice] → true → no revert
  // - Bob's tokens were pulled (payer = bob)
  // - Alice received LP shares
  // - Bob (non-allowlisted) successfully deposited into a restricted pool
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L172-177)
```text
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L85-87)
```text
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
    }
```
