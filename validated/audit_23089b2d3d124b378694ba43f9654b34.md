### Title
Unchecked `transferFrom` Return Value Enables Draining usdcE from DirectDepositV1 Contracts — (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` calls `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` without checking the returned boolean. Because the subsequent steps unconditionally drain usdcE from the `DirectDepositV1` contract and forward it to the caller, a silent `transferFrom` failure allows any unprivileged caller to extract usdcE from a victim subaccount's deposit address without providing any USDC in return.

---

### Finding Description

`replaceUsdcEWithUsdc` is an `external` function with no access control beyond a chain-ID gate (`block.chainid == 57073`). Its intended logic is a 1-for-1 swap: the caller supplies USDC into the `DirectDepositV1` address, and receives the equivalent usdcE back.

The critical path is:

```solidity
// ContractOwner.sol lines 615-619
uint256 balance = IERC20Base(usdcE).balanceOf(directDepositV1);
if (balance > 0) {
    IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance); // ← return value discarded
    DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));       // sends usdcE → ContractOwner
    IERC20Base(usdcE).safeTransfer(msg.sender, balance);                 // sends usdcE → caller
}
``` [1](#0-0) 

`IERC20Base.transferFrom` is declared to return `bool`: [2](#0-1) 

The project already has `ERC20Helper.safeTransferFrom`, which correctly checks the return value and reverts on failure: [3](#0-2) 

`ContractOwner` imports and uses `ERC20Helper` via `using ERC20Helper for IERC20Base` (line 24), and even uses `safeTransfer` on line 618 in the same function — making the omission on line 616 an inconsistency within the same code block. [4](#0-3) 

`DirectDepositV1.withdraw` is `onlyOwner`, and `ContractOwner` is the owner, so the call on line 617 succeeds unconditionally, transferring all usdcE from the deposit address to `ContractOwner`. The final `safeTransfer` then forwards it to the caller. [5](#0-4) 

---

### Impact Explanation

If `transferFrom` returns `false` instead of reverting (as some non-standard ERC20 tokens do), the function continues executing. The attacker receives the full usdcE balance of the targeted `DirectDepositV1` contract without depositing any USDC. The subaccount owner loses their usdcE collateral that was staged for deposit. This is a direct, concrete asset delta: usdcE moves from a victim's deposit address to the attacker's wallet with zero cost.

---

### Likelihood Explanation

The function is callable by any address on chain 57073 (Ink). The hardcoded USDC address (`0x2D270e6886d130D724215A266106e6832161EAEd`) is the token whose `transferFrom` must fail silently for the exploit to work. If that token ever behaves non-standardly (returns `false` on insufficient allowance rather than reverting), or if the attacker has zero allowance set, the exploit is immediately triggerable. Even under standard USDC behavior, the missing check is a latent correctness bug that violates the swap invariant and could be triggered by any future token upgrade or replacement.

---

### Recommendation

Replace the bare `transferFrom` call on line 616 with the project's own `safeTransferFrom` from `ERC20Helper`, consistent with how `safeTransfer` is already used on line 618:

```solidity
// Before (line 616):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After:
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
``` [6](#0-5) 

---

### Proof of Concept

1. A `DirectDepositV1` contract exists for some `subaccount` and holds `N` usdcE tokens.
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)` with zero USDC allowance granted to `ContractOwner`.
3. `IERC20Base(usdc).transferFrom(attacker, directDepositV1, N)` returns `false` (no allowance) — return value is not checked, execution continues.
4. `DirectDepositV1.withdraw(usdcE)` transfers `N` usdcE from `directDepositV1` → `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(attacker, N)` transfers `N` usdcE from `ContractOwner` → attacker.
6. Attacker receives `N` usdcE; the subaccount's deposit address is emptied; no USDC was ever deposited. [7](#0-6)

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

**File:** core/contracts/interfaces/IERC20Base.sol (L25-29)
```text
    function transferFrom(
        address from,
        address to,
        uint256 amount
    ) external returns (bool);
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
