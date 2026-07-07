### Title
Unchecked `transferFrom` Return Value in `replaceUsdcEWithUsdc` Enables Silent Failure and usdcE Drain from DDAs — (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` calls `IERC20Base(usdc).transferFrom` without checking its `bool` return value. If the transfer fails silently (returns `false` rather than reverting), execution continues: usdcE is withdrawn from the victim's Direct Deposit Account (DDA) and transferred to the caller. The caller receives usdcE without ever depositing usdc, draining the DDA of its collateral.

---

### Finding Description

`replaceUsdcEWithUsdc` is an `external` function with no access control, callable by any address on chain 57073 (Ink Chain):

```solidity
function replaceUsdcEWithUsdc(bytes32 subaccount) external {
    require(block.chainid == 57073, ERR_UNAUTHORIZED);
    address payable directDepositV1 = directDepositV1Address[subaccount];
    require(directDepositV1 != address(0), "no dda");
    address usdcE = 0xF1815bd50389c46847f0Bda824eC8da914045D14;
    address usdc  = 0x2D270e6886d130D724215A266106e6832161EAEd;
    uint256 balance = IERC20Base(usdcE).balanceOf(directDepositV1);
    if (balance > 0) {
        IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance); // ← return value ignored
        DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
        IERC20Base(usdcE).safeTransfer(msg.sender, balance);
    }
}
``` [1](#0-0) 

The intended flow is:
1. Caller deposits `balance` usdc into the DDA.
2. The DDA's usdcE is withdrawn to `ContractOwner`.
3. `ContractOwner` forwards the usdcE to the caller.

The broken flow when `transferFrom` returns `false`:
1. Step 1 silently fails — no usdc is deposited.
2. Steps 2–3 still execute — usdcE is drained from the DDA and sent to the caller.

**Root cause**: `IERC20Base(usdc).transferFrom` is called directly, bypassing the protocol's own `ERC20Helper.safeTransferFrom` wrapper, which enforces the return value check. `ContractOwner` already imports and uses `ERC20Helper` via `using ERC20Helper for IERC20Base` at line 24, making the omission an oversight. [2](#0-1) [3](#0-2) 

The safe wrapper that should have been used:

```solidity
// ERC20Helper.safeTransferFrom — checks success AND decoded bool
require(
    success && (data.length == 0 || abi.decode(data, (bool))),
    ERR_TRANSFER_FAILED
);
``` [4](#0-3) 

---

### Impact Explanation

Any DDA holding usdcE can be drained by an unprivileged caller. The attacker:
- Pays nothing (no usdc deposited).
- Receives the full usdcE balance of the targeted DDA.
- The subaccount owner loses their usdcE collateral permanently.

The corrupted state delta is exact: `usdcE.balanceOf(directDepositV1)` drops to zero while the attacker's usdcE balance increases by the same amount, with no corresponding usdc credit to the DDA.

---

### Likelihood Explanation

The function is `external` with no access control, reachable by any caller on chain 57073. The exploitability depends on whether the specific usdc token at `0x2D270e6886d130D724215A266106e6832161EAEd` returns `false` on a failed transfer rather than reverting. Many bridged or wrapped token implementations on non-mainnet chains do not follow the strict revert-on-failure convention. Even if the current deployment reverts, the code is structurally incorrect and becomes exploitable if the token is upgraded or redeployed with non-reverting failure semantics.

---

### Recommendation

Replace the bare `transferFrom` call with the protocol's existing safe wrapper:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

`safeTransferFrom` is already available on `IERC20Base` via the `using ERC20Helper for IERC20Base` directive in `ContractOwner`. [5](#0-4) 

---

### Proof of Concept

1. Identify a `subaccount` whose DDA (`directDepositV1Address[subaccount]`) holds a non-zero usdcE balance.
2. Call `ContractOwner.replaceUsdcEWithUsdc(subaccount)` from an address with zero usdc balance or zero usdc allowance to `ContractOwner`.
3. If the usdc token returns `false` on the failed `transferFrom` (rather than reverting), execution continues past line 616.
4. `DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE))` transfers the full usdcE balance from the DDA to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` forwards the usdcE to the attacker.
6. Attacker receives `balance` usdcE; the DDA is emptied; no usdc was deposited.

### Citations

**File:** core/contracts/ContractOwner.sol (L24-24)
```text
    using ERC20Helper for IERC20Base;
```

**File:** core/contracts/ContractOwner.sol (L608-620)
```text
    function replaceUsdcEWithUsdc(bytes32 subaccount) external {
        require(block.chainid == 57073, ERR_UNAUTHORIZED);
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        address usdcE = 0xF1815bd50389c46847f0Bda824eC8da914045D14;
        address usdc = 0x2D270e6886d130D724215A266106e6832161EAEd;
        uint256 balance = IERC20Base(usdcE).balanceOf(directDepositV1);
        if (balance > 0) {
            IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
            IERC20Base(usdcE).safeTransfer(msg.sender, balance);
        }
    }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-41)
```text
    function safeTransferFrom(
        IERC20Base self,
        address from,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(
                IERC20Base.transferFrom.selector,
                from,
                to,
                amount
            )
        );

        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
```
