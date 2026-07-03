import { createContext } from "react";

export interface ProfileContextValue {
  profile: string;
  currentProfile: string;
  profiles: string[];
  setProfile: (name: string) => void;
}

export const ProfileContext = createContext<ProfileContextValue>({
  profile: "",
  currentProfile: "default",
  profiles: [],
  setProfile: () => {},
});
