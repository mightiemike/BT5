### Title
Unchecked `transferFrom` Return Value in `replaceUsdcEWithUsdc` Enables USDC.e Theft from DirectDepositV1 Contracts — (`File: core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` is a permissionless function that swaps USDC.e for USDC inside a `DirectDepositV1` (DDA) contract. It calls raw `transferFrom` on the USDC token without checking the return value. If the USDC token on chain 57073 (Ink) returns `false` on a failed transfer instead of reverting, an attacker can drain USDC.e from any DDA without providing any USDC.

---

### Finding Description

`replaceUsdcEWithUsdc` is declared `external` with no access control beyond a chain ID check: [1](#0-0) 

The three-step body is:
1. **Line 616** — raw `transferFrom` (return value discarded): pull USDC from `msg.sender` into the DDA.
2. **Line 617** — `withdraw(usdcE)`: pull all USDC.e out of the DDA into `ContractOwner`.
3. **Line 618** — `safeTransfer(msg.sender, balance)`: push USDC.e from `ContractOwner` to `msg.sender`.

If step 1 silently fails (returns `false` without reverting), steps 2 and 3 still execute. The attacker receives USDC.e without ever transferring USDC.

The contract already declares `using ERC20Helper for IERC20Base` and the library provides `safeTransferFrom` that checks the return value: [2](#0-1) [3](#0-2) 

The inconsistency is stark: line 618 uses `safeTransfer` on USDC.e, but line 616 uses raw `transferFrom` on USDC. [4](#0-3) 

---

### Impact Explanation

An attacker who calls `replaceUsdcEWithUsdc(subaccount)` for any DDA that holds USDC.e, while holding zero USDC (or having zero allowance), will:

- Receive the full USDC.e balance of the DDA for free.
- Leave the DDA empty of USDC.e, with no USDC deposited in its place.
- Corrupt the intended migration invariant: the DDA subaccount owner loses their USDC.e collateral.

The `DirectDepositV1.withdraw` call transfers USDC.e to `ContractOwner` (the caller of `withdraw`), and then `safeTransfer` forwards it to the attacker — both steps are unconditional on the success of step 1. [5](#0-4) 

---

### Likelihood Explanation

The function is callable by any address on chain 57073 (Ink). The USDC address hardcoded is `0x2D270e6886d130D724215A266106e6832161EAEd`. Whether this specific deployment is a no-revert-on-failure token determines exploitability. Regardless, the unchecked return value is a latent vulnerability: any future token upgrade, proxy swap, or redeployment to a chain where the same address resolves to a non-standard ERC20 makes this immediately exploitable. The attack requires no privileges, no capital, and no complex setup — just a call to a public function.

---

### Recommendation

Replace the raw `transferFrom` on line 616 with `safeTransferFrom` from the already-imported `ERC20Helper` library:

```solidity
// Before (vulnerable):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
``` [3](#0-2) 

---

### Proof of Concept

1. A DDA for `subaccount` holds 1000 USDC.e (`usdcE` balance > 0).
2. Attacker has 0 USDC and 0 USDC allowance to `ContractOwner`.
3. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)` on chain 57073.
4. Line 616: `IERC20Base(usdc).transferFrom(attacker, directDepositV1, 1000)` — returns `false`, no revert, no USDC moved.
5. Line 617: `DirectDepositV1(directDepositV1).withdraw(usdcE)` — 1000 USDC.e transferred from DDA → `ContractOwner`.
6. Line 618: `IERC20Base(usdcE).safeTransfer(attacker, 1000)` — 1000 USDC.e transferred from `ContractOwner` → attacker.
7. Attacker holds 1000 USDC.e; DDA holds 0; subaccount owner's collateral is gone. [6](#0-5)

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

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```
