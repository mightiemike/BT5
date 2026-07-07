### Title
Unchecked Return Value on `transferFrom` in `replaceUsdcEWithUsdc` Enables Token Theft — (`File: core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` uses a raw `.transferFrom()` call without checking its return value, while the rest of the codebase consistently uses `ERC20Helper.safeTransferFrom`. If the USDC token at the hardcoded address returns `false` on failure (a valid ERC20 behavior), the function silently proceeds, withdrawing USDC-E from a victim's `DirectDepositV1` contract and sending it to the caller — without the caller ever providing USDC.

---

### Finding Description

`ContractOwner.sol` declares `using ERC20Helper for IERC20Base` at line 24, giving it access to the `safeTransferFrom` wrapper that checks return values via low-level `.call()`. However, `replaceUsdcEWithUsdc` at line 616 uses the raw `.transferFrom()` call instead:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
IERC20Base(usdcE).safeTransfer(msg.sender, balance);
```

The function is designed as a token swap: the caller provides USDC (step 1), the DDA's USDC-E is pulled to `ContractOwner` (step 2), and USDC-E is forwarded to the caller (step 3). If step 1 fails silently (returns `false` without reverting), steps 2 and 3 still execute. The caller receives USDC-E from `ContractOwner` without having provided any USDC.

The function has no access control beyond a chain ID check (`require(block.chainid == 57073)`), making it callable by any address on the Ink chain. [1](#0-0) 

The `ERC20Helper.safeTransferFrom` wrapper, used everywhere else in the codebase, performs a low-level `.call()` and requires `success && (data.length == 0 || abi.decode(data, (bool)))`, catching both reverts and `false` returns. [2](#0-1) 

The `using ERC20Helper for IERC20Base` directive is active in `ContractOwner`, making the safe variant directly available but unused here. [3](#0-2) 

---

### Impact Explanation

If the USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` (Ink chain) returns `false` on a failed `transferFrom` (e.g., insufficient allowance or balance) rather than reverting, an attacker can:

1. Call `replaceUsdcEWithUsdc(subaccount)` for any subaccount whose `DirectDepositV1` holds a USDC-E balance.
2. The raw `transferFrom` fails silently — no USDC is taken from the attacker.
3. `DirectDepositV1(directDepositV1).withdraw(usdcE)` still executes, pulling the victim's USDC-E into `ContractOwner`.
4. `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` sends that USDC-E to the attacker.

The attacker drains USDC-E from any `DirectDepositV1` contract with a balance, at zero cost. The corrupted asset delta is: victim's USDC-E balance → attacker, with no USDC transferred in return.

---

### Likelihood Explanation

The function is permissionless on Ink chain (chain ID 57073). The USDC address is a hardcoded deployment on a relatively new chain; bridged or wrapped USDC variants sometimes return `false` on failure rather than reverting. The inconsistency is concrete — the same contract imports and uses `ERC20Helper.safeTransferFrom` in all other token operations but omits it here — making this an exploitable pattern whenever the token's failure mode is a `false` return rather than a revert.

---

### Recommendation

Replace the raw `.transferFrom()` call with the `ERC20Helper.safeTransferFrom` wrapper already available in scope:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
``` [4](#0-3) 

---

### Proof of Concept

1. A `DirectDepositV1` exists for `subaccount` with 1000 USDC-E balance.
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)` with zero USDC allowance granted to `ContractOwner`.
3. `IERC20Base(usdc).transferFrom(attacker, directDepositV1, 1000)` returns `false` (no revert) due to zero allowance.
4. Execution continues: `DirectDepositV1.withdraw(usdcE)` transfers 1000 USDC-E from the DDA to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(attacker, 1000)` sends 1000 USDC-E to the attacker.
6. Attacker receives 1000 USDC-E; victim's DDA is drained; no USDC was provided. [1](#0-0)

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
