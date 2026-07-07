### Title
Unchecked `transferFrom` Return Value in `replaceUsdcEWithUsdc` Enables usdcE Drain from DDAs — (File: `core/contracts/ContractOwner.sol`)

---

### Summary
`ContractOwner.replaceUsdcEWithUsdc()` calls `IERC20Base(usdc).transferFrom(...)` without checking its boolean return value. Because the function is permissionlessly callable by any address on chain 57073, and because `ERC20Helper.safeTransferFrom` is already available in scope but unused here, a non-compliant USDC token that returns `false` instead of reverting would allow any caller to drain usdcE from a victim's DirectDepositV1 address without providing any USDC in exchange.

---

### Finding Description

`ContractOwner` declares `using ERC20Helper for IERC20Base` at line 24, giving every `IERC20Base` instance access to `safeTransferFrom`, which low-level-calls `transferFrom` and requires the return value to be `true`. [1](#0-0) 

Despite this, `replaceUsdcEWithUsdc` calls the raw `IERC20Base.transferFrom` at line 616 and discards the `bool` return: [2](#0-1) 

The function's only guard is a chain-id check; there is no role or ownership restriction: [3](#0-2) 

The execution sequence after the unchecked `transferFrom` unconditionally:
1. Calls `DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE))` — pulling all usdcE from the DDA into `ContractOwner`.
2. Calls `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` — forwarding that usdcE to the caller. [4](#0-3) 

If the USDC token returns `false` on a failed `transferFrom` rather than reverting, the caller receives the DDA's entire usdcE balance while contributing zero USDC.

The safe counterpart `ERC20Helper.safeTransferFrom` is defined and would have caught this: [5](#0-4) 

---

### Impact Explanation

An attacker can steal the full usdcE balance of any DDA that has been registered via `directDepositV1Address`. The stolen asset is usdcE (a bridged stablecoin), so the impact is direct loss of user collateral held in the DDA pending deposit. The `safeTransfer` on line 618 uses the checked helper, so the usdcE leg of the swap always executes; only the USDC leg is unguarded.

---

### Likelihood Explanation

The function is callable by any EOA or contract on Ink (chain 57073) with no role restriction. The USDC address is hardcoded to `0x2D270e6886d130D724215A266106e6832161EAEd` on Ink — a chain where token implementations may differ from mainnet Circle USDC. If that deployment returns `false` on insufficient-allowance `transferFrom` rather than reverting (a known pattern in some ERC20 variants), the exploit is immediately executable. The attacker needs only to know a subaccount whose DDA holds usdcE.

---

### Recommendation

Replace the raw `transferFrom` call with the already-imported `ERC20Helper.safeTransferFrom`, consistent with how the rest of the codebase handles token transfers:

```solidity
// Before (line 616)
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
``` [6](#0-5) 

Similarly, the raw `approve` calls at lines 254, 530–531 in `ContractOwner` and line 92 in `DirectDepositV1.creditDeposit()` should be wrapped with a checked helper, as `ERC20Helper` does not currently expose a `safeApprove`. [7](#0-6) [8](#0-7) 

---

### Proof of Concept

1. A DDA for `subaccount` holds `N` usdcE (deposited by a legitimate user awaiting `creditDeposit`).
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)` on chain 57073.
3. `IERC20Base(usdc).transferFrom(attacker, directDepositV1, N)` is called. Attacker has given zero USDC allowance; the non-compliant USDC token returns `false` instead of reverting.
4. The return value is not checked; execution continues.
5. `DirectDepositV1(directDepositV1).withdraw(usdcE)` transfers `N` usdcE from the DDA to `ContractOwner`.
6. `IERC20Base(usdcE).safeTransfer(attacker, N)` sends `N` usdcE to the attacker.
7. Attacker has received `N` usdcE; the DDA is empty; the legitimate user's collateral is gone.

### Citations

**File:** core/contracts/ContractOwner.sol (L24-24)
```text
    using ERC20Helper for IERC20Base;
```

**File:** core/contracts/ContractOwner.sol (L530-531)
```text
            assetToken.approve(tokenAddr, 0);
            assetToken.approve(tokenAddr, assetBalance);
```

**File:** core/contracts/ContractOwner.sol (L608-611)
```text
    function replaceUsdcEWithUsdc(bytes32 subaccount) external {
        require(block.chainid == 57073, ERR_UNAUTHORIZED);
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
```

**File:** core/contracts/ContractOwner.sol (L615-619)
```text
        if (balance > 0) {
            IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
            IERC20Base(usdcE).safeTransfer(msg.sender, balance);
        }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-42)
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
    }
```

**File:** core/contracts/DirectDepositV1.sol (L92-92)
```text
                token.approve(address(endpoint), balance);
```
