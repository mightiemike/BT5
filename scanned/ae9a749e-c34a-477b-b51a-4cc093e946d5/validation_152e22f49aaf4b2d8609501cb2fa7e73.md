### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Unlisted Addresses to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is designed to gate who may add liquidity to a pool. Its `beforeAddLiquidity` hook silently drops the `sender` parameter (the actual token-providing caller) and instead validates only the `owner` (the position recipient). Any address not on the allowlist can bypass the guard entirely by calling `addLiquidity` with a listed address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct actors to the extension hook:

- `sender` = `msg.sender` — the address that calls `addLiquidity` and provides tokens via the liquidity callback
- `owner` — the address that will own the resulting LP position [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both: [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter (`sender`) is unnamed and therefore silently discarded. The guard only checks `owner`: [3](#0-2) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly names and checks `sender` (the actual swapper), ignoring `recipient`: [4](#0-3) 

The asymmetry is the root cause: the deposit guard never evaluates the address that actually provides tokens.

---

### Impact Explanation

An address not on the allowlist (`sender`) can call `addLiquidity(listed_owner, salt, deltas, ...)`. The extension checks `allowedDepositor[pool][listed_owner]`, which passes. The unlisted `sender` then satisfies the liquidity callback, depositing tokens into the pool. The pool accepts tokens from an address the pool admin explicitly excluded. The deposit allowlist — a core admin-configured security boundary — is rendered ineffective for the actual token provider. This matches the "Allowlist path: deposit/swap allowlist checks must cover the exact actor/action intended and cannot be bypassed through … owner/salt separation" criterion in the allowed impact gate.

---

### Likelihood Explanation

The bypass requires only that the attacker knows one allowlisted address (trivially discoverable via `AllowedToDepositSet` events or `allowedDepositor` reads). No special role, flash loan, or privileged access is needed. Any EOA or contract can execute it in a single transaction.

---

### Recommendation

Replace the `owner` check with a `sender` check, mirroring `SwapAllowlistExtension`:

```solidity
// DepositAllowlistExtension.sol
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
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

If the intent is to restrict both the depositor and the owner, both should be checked independently.

---

### Proof of Concept

```
Setup:
  pool P has DepositAllowlistExtension E configured with BEFORE_ADD_LIQUIDITY_ORDER pointing to E
  Alice (0xAlice) is allowlisted: allowedDepositor[P][0xAlice] = true
  Bob   (0xBob)  is NOT allowlisted

Attack:
  Bob calls P.addLiquidity(owner=0xAlice, salt, deltas, callbackData, extensionData)

  Pool calls E.beforeAddLiquidity(sender=0xBob, owner=0xAlice, ...)
    → check: allowedDepositor[P][0xAlice] == true  ✓  (passes)

  Pool calls LiquidityLib.addLiquidity(owner=0xAlice, ...)
    → callback fires on 0xBob; Bob transfers tokens to pool
    → Alice receives LP shares

Result:
  Bob (unlisted) successfully deposited tokens into the allowlisted pool.
  The deposit allowlist guard was never evaluated against the actual depositor.
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
